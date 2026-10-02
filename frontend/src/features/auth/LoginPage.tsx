import { Navigate, useNavigate } from "@tanstack/react-router"
import { type FormEvent, useState } from "react"

import { useAuth } from "./AuthProvider"

export function LoginPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (auth.status === "authenticated") return <Navigate to="/" />

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    const data = new FormData(event.currentTarget)
    try {
      await auth.login({
        email: String(data.get("email")),
        password: String(data.get("password")),
        organization_slug: String(data.get("organization")),
      })
      await navigate({ to: "/" })
    } catch {
      setError("Authentication failed.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login-layout">
      <section className="login-story">
        <a className="brand" href="/">
          RAGelit
        </a>
        <p className="eyebrow">Private knowledge infrastructure</p>
        <h1>Private knowledge, controlled.</h1>
        <p>
          Search company documents with traceable answers while tenant and role
          boundaries stay enforced at every layer.
        </p>
      </section>
      <section className="login-panel" aria-labelledby="login-title">
        <p className="eyebrow">Secure workspace</p>
        <h2 id="login-title">Welcome back</h2>
        <p>Use your organization credentials to continue.</p>
        <form onSubmit={submit}>
          {auth.sessionMessage && (
            <p
              role={auth.revocationFailed ? "alert" : "status"}
              className="form-error"
            >
              {auth.sessionMessage}
            </p>
          )}
          {auth.revocationFailed && (
            <button
              type="button"
              disabled={auth.logoutPending}
              onClick={() => void auth.logout()}
            >
              Retry log out
            </button>
          )}
          <label>
            Work email
            <input name="email" type="email" autoComplete="email" required />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <label>
            Organization
            <input name="organization" defaultValue="northstar-labs" required />
          </label>
          {error ? (
            <p role="alert" className="form-error">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={submitting || auth.logoutPending || auth.revocationFailed}
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  )
}
