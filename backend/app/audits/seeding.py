from collections.abc import Mapping
from typing import cast
from uuid import UUID, uuid4, uuid5

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audits.fixtures import FixtureTemplate
from app.audits.workspace import (
    AuditWorkspace,
    AuditWorkspaceError,
    DocumentBinding,
    FixtureBindings,
)
from app.chat.contracts import Generation
from app.core.security import hash_password
from app.documents.models import DocumentVersion
from app.identity.models import User
from app.retrieval.contracts import AuthorizedChunk
from app.tenancy.models import Membership, Organization
from app.workers.ingestion import run_once


class FixtureCitingProvider:
    identifier = "fixture-citing-v1"

    def generate(
        self, question: str, context: tuple[AuthorizedChunk, ...]
    ) -> Generation:
        return Generation(
            "\n".join(chunk.text for chunk in context),
            tuple(chunk.id for chunk in context),
        )


def login_actor(
    workspace: AuditWorkspace,
    *,
    email: str,
    organization_slug: str,
) -> dict[str, str]:
    with workspace.mutation():
        response = workspace.client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": workspace.config.fixture_password.get_secret_value(),
                "organization_slug": organization_slug,
            },
        )
        if response.status_code != 200:
            raise AuditWorkspaceError("audit_login_failed")
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise AuditWorkspaceError("audit_login_failed")
    return {"Authorization": f"Bearer {token}"}


def ingest_document(
    workspace: AuditWorkspace, organization_id: UUID, document_id: UUID
) -> DocumentBinding:
    with workspace.mutation():
        if not run_once(
            organization_id,
            factory=workspace.factory,
            settings=workspace.settings,
            store=workspace.store,
            embeddings=workspace.embeddings,
        ):
            raise AuditWorkspaceError("audit_ingestion_failed")
    with workspace.admin_engine.connect() as connection:
        versions = (
            connection.execute(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.state == "ready",
                )
            )
            .mappings()
            .all()
        )
        if len(versions) != 1:
            raise AuditWorkspaceError("audit_ingestion_failed")
        version = versions[0]
        if version["chunk_count"] != 1:
            raise AuditWorkspaceError("audit_ingestion_failed")
        return DocumentBinding(
            document_id=document_id,
            version_id=version["id"],
            chunk_ids=(uuid5(version["id"], "0"),),
            content_hash=version["checksum"],
        )


def verify_probe(
    workspace: AuditWorkspace,
    headers: Mapping[str, str],
    question: str,
    binding: DocumentBinding,
) -> None:
    with workspace.mutation():
        response = workspace.client.post(
            "/api/v1/chat/query",
            headers=headers,
            json={"question": question, "limit": 20},
        )
        if response.status_code != 200 or response.json().get("status") != "answered":
            raise AuditWorkspaceError("audit_probe_unavailable")
        cited = {
            UUID(citation["chunk_id"]) for citation in response.json()["citations"]
        }
        if not set(binding.chunk_ids).issubset(cited):
            raise AuditWorkspaceError("audit_probe_unavailable")


def seed_workspace(
    workspace: AuditWorkspace, template: FixtureTemplate
) -> FixtureBindings:
    workspace.validate_owned()
    if template.checksum != workspace.template.checksum:
        raise AuditWorkspaceError("audit_fixture_drift")
    if workspace.bindings.seeded:
        return workspace.bindings
    if workspace.bindings.organizations or any(workspace.bindings.rows.values()):
        raise AuditWorkspaceError("audit_seed_incomplete")
    password_hash = hash_password(workspace.config.fixture_password.get_secret_value())
    with workspace.mutation():
        with Session(workspace.admin_engine) as session:
            organizations = {org.id: uuid4() for org in template.organizations}
            actors = {actor.id: uuid4() for actor in template.actors}
            memberships = {actor.id: uuid4() for actor in template.actors}
            session.add_all(
                [
                    Organization(id=organizations[org.id], name=org.id, slug=org.slug)
                    for org in template.organizations
                ]
            )
            session.add_all(
                [
                    User(
                        id=actors[actor.id],
                        email=actor.email,
                        password_hash=password_hash,
                    )
                    for actor in template.actors
                ]
            )
            session.flush()
            session.add_all(
                [
                    Membership(
                        id=memberships[actor.id],
                        organization_id=organizations[actor.organization_id],
                        user_id=actors[actor.id],
                        role=actor.role,
                    )
                    for actor in template.actors
                ]
            )
            session.commit()
        workspace.bindings = workspace.bindings.model_copy(
            update={
                "organizations": organizations,
                "actors": actors,
                "memberships": memberships,
            }
        )
        workspace.store.ensure_tenants(tuple(organizations.values()))
    cast(
        FastAPI, workspace.client.app
    ).state.generation_provider = FixtureCitingProvider()
    headers: dict[str, dict[str, str]] = {}
    actor_by_id = {actor.id: actor for actor in template.actors}
    org_by_id = {org.id: org for org in template.organizations}

    def actor_headers(actor_id: str) -> dict[str, str]:
        if actor_id not in headers:
            actor = actor_by_id[actor_id]
            headers[actor_id] = login_actor(
                workspace,
                email=actor.email,
                organization_slug=org_by_id[actor.organization_id].slug,
            )
        return headers[actor_id]

    for group in template.groups:
        org_id = organizations[group.organization_id]
        manager = actor_headers(f"{group.organization_id}-owner")
        with workspace.mutation():
            response = workspace.client.post(
                f"/api/v1/organizations/{org_id}/groups",
                headers=manager,
                json={"name": group.name},
            )
            if response.status_code != 201:
                raise AuditWorkspaceError("audit_group_failed")
            group_id = UUID(response.json()["id"])
            workspace.bindings = workspace.bindings.model_copy(
                update={"groups": workspace.bindings.groups | {group.id: group_id}}
            )
        for actor in template.actors:
            if actor.group_id != group.id:
                continue
            with workspace.mutation():
                response = workspace.client.post(
                    f"/api/v1/organizations/{org_id}/groups/{group_id}/members/{memberships[actor.id]}",
                    headers=manager,
                )
                if response.status_code != 204:
                    raise AuditWorkspaceError("audit_group_failed")
    for document in template.documents:
        manager = actor_headers(f"{document.organization_id}-owner")
        with workspace.mutation():
            response = workspace.client.post(
                "/api/v1/documents",
                params={"filename": f"{document.id}.txt"},
                headers=manager | {"Content-Type": "text/plain"},
                content=document.text.encode("utf-8"),
            )
            if response.status_code != 202:
                raise AuditWorkspaceError("audit_upload_failed")
            document_id = UUID(response.json()["id"])
        with workspace.mutation():
            response = workspace.client.patch(
                f"/api/v1/documents/{document_id}",
                headers=manager,
                json={
                    "visibility": document.visibility,
                    "user_ids": [
                        str(actors[identifier]) for identifier in document.user_ids
                    ],
                    "group_ids": [
                        str(workspace.bindings.groups[identifier])
                        for identifier in document.group_ids
                    ],
                },
            )
            if response.status_code != 200:
                raise AuditWorkspaceError("audit_grant_failed")
        binding = ingest_document(
            workspace, organizations[document.organization_id], document_id
        )
        with workspace.mutation():
            workspace.bindings = workspace.bindings.model_copy(
                update={
                    "documents": workspace.bindings.documents | {document.id: binding}
                }
            )
        verify_probe(
            workspace,
            actor_headers(document.control_actor_id),
            document.question,
            binding,
        )
    with workspace.mutation():
        workspace.bindings = workspace.bindings.model_copy(update={"seeded": True})
    return workspace.bindings
