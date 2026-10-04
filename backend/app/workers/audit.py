import argparse
import json
import os
import time
from collections.abc import Callable
from typing import NoReturn
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.audit_jobs.claims import (
    claim_audit,
    finish_audit_claim,
    mark_recovery_required,
    renew_audit_claim,
    requester_can_run,
    resolve_interrupted_audit,
)
from app.audit_jobs.contracts import CliOutcome
from app.audit_jobs.execution import (
    AuditWorkerConfiguration,
    configuration_from_environment,
    run_cli,
)
from app.audit_jobs.service import AuditJobError
from app.db.session import build_engine, build_session_factory

_LOCK_KEY = 0x524147454C4954


def run_once(
    org_id: UUID,
    *,
    session_factory: Callable[[], Session],
    configuration: AuditWorkerConfiguration,
) -> bool:
    with session_factory() as session:
        engine = session.get_bind()
        if not isinstance(engine, Engine):
            raise ValueError("audit_worker_engine_required")
    # A pooled session would return its advisory-locked connection on commit.
    with engine.connect() as lock:
        acquired = lock.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}
        )
        lock.commit()
        if not acquired:
            return False
        try:
            with session_factory() as session:
                claim = claim_audit(org_id, session=session)
            if claim is None:
                return False
            with session_factory() as session:
                allowed = requester_can_run(
                    claim.requester_user_id, org_id, session=session
                )
            if not allowed:
                outcome = CliOutcome(2, error_code="audit_requester_revoked")
            else:

                def heartbeat() -> bool:
                    lock.execute(text("SELECT 1"))
                    lock.commit()
                    with session_factory() as session:
                        return renew_audit_claim(claim, session=session)

                outcome = run_cli(configuration, claim.request_id, heartbeat=heartbeat)
            with session_factory() as session:
                if outcome.recovery_required or not finish_audit_claim(
                    claim, outcome, session=session
                ):
                    mark_recovery_required(claim, session=session)
            if outcome.error_code == "audit_interrupted":
                raise KeyboardInterrupt
            return True
        finally:
            try:
                lock.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY}
                )
                lock.commit()
            except Exception:
                lock.invalidate()
                raise


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("audit_invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Run safe synthetic audits for one organization.")
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--recover-run", type=UUID)
    parser.add_argument("--confirm-worker-stopped", action="store_true")
    engine: Engine | None = None
    try:
        args = parser.parse_args(argv)
        if bool(args.recover_run) != args.confirm_worker_stopped or (
            args.recover_run and args.once
        ):
            raise ValueError("audit_invalid_arguments")
        database_url = os.environ["RAGELIT_DATABASE_URL"]
        engine = build_engine(database_url)
        factory = build_session_factory(engine)
        if args.recover_run:
            with factory() as session:
                resolve_interrupted_audit(
                    args.recover_run,
                    args.organization_id,
                    session=session,
                    worker_stopped_confirmed=True,
                )
            print(json.dumps({"code": "audit_recovery_complete", "exit_code": 2}))
            return 2
        configuration = configuration_from_environment()
        while True:
            processed = run_once(
                args.organization_id,
                session_factory=factory,
                configuration=configuration,
            )
            if args.once:
                return 0
            if not processed:
                time.sleep(2)
    except KeyboardInterrupt:
        code = "audit_interrupted"
    except AuditJobError as error:
        code = error.code
    except ValueError as error:
        code = (
            "audit_invalid_arguments"
            if str(error) == "audit_invalid_arguments"
            else "audit_invalid_configuration"
        )
    except Exception:
        code = "audit_worker_failed"
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps({"code": code, "exit_code": 2}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
