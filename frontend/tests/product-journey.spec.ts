import { expect, type Page, test } from "@playwright/test"
import type { components } from "../src/api/generated/schema"

async function login(
  page: Page,
  email: string,
  organization = "northstar-labs",
) {
  await page.goto("/login")
  await page.getByLabel("Work email").fill(email)
  await page
    .getByLabel("Password", { exact: true })
    .fill("task-nine-test-password")
  await page.getByLabel("Organization", { exact: true }).fill(organization)
  await page.getByRole("button", { name: "Sign in", exact: true }).click()
  await expect(page.getByLabel("Organization switcher")).toBeVisible()
}
async function upload(page: Page, name: string, text: string) {
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page
    .getByLabel("Document file", { exact: true })
    .setInputFiles({ name, mimeType: "text/plain", buffer: Buffer.from(text) })
  await page
    .getByRole("button", { name: "Upload document", exact: true })
    .click()
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible()
  await expect(page.getByRole("status")).toContainText("Ready", {
    timeout: 30_000,
  })
  return page.url()
}
async function query(page: Page, question: string) {
  await page.getByRole("link", { name: "Chat", exact: true }).click()
  await expect(
    page.getByRole("textbox", { name: "Your question", exact: true }),
  ).toBeEnabled()
  await page
    .getByRole("textbox", { name: "Your question", exact: true })
    .fill(question)
  const response = page.waitForResponse("**/api/v1/chat/query")
  await page.getByRole("button", { name: "Ask question", exact: true }).click()
  const returned = await response
  expect(returned.status()).toBe(200)
  return (await returned.json()) as components["schemas"]["AnswerResponse"]
}
async function trace(page: Page) {
  const response = page.waitForResponse("**/api/v1/query-runs/*")
  await page
    .getByRole("button", { name: "View query trace", exact: true })
    .click()
  const returned = await response
  expect(returned.status()).toBe(200)
  return (await returned.json()) as components["schemas"]["TraceResponse"]
}

test("real stores enforce group grants tenant isolation and revocation", async ({
  page,
  browser,
}) => {
  test.setTimeout(120_000)
  await login(page, "owner@northstar.example")
  await page.getByRole("link", { name: "Groups", exact: true }).click()
  await page
    .getByLabel("New group name", { exact: true })
    .fill("JourneyAllowed")
  await page.getByRole("button", { name: "Create group", exact: true }).click()
  await page
    .getByRole("button", { name: "Members for JourneyAllowed", exact: true })
    .click()
  await page
    .getByRole("combobox", { name: "Add person", exact: true })
    .selectOption({ label: "member@northstar.example" })
  await page.getByRole("button", { name: "Add member", exact: true }).click()
  await expect(
    page.getByRole("checkbox", {
      name: "member@northstar.example",
      exact: true,
    }),
  ).toBeChecked()
  const documentUrl = await upload(
    page,
    "journey-policy.txt",
    "Northstar leave policy permits twenty days. PRIVATE_NORTHSTAR_JOURNEY.",
  )
  await page.getByRole("button", { name: "Edit access", exact: true }).click()
  await page
    .getByRole("checkbox", { name: "JourneyAllowed", exact: true })
    .check()
  await page.getByRole("button", { name: "Save access", exact: true }).click()
  await expect(page.getByText("Access saved", { exact: true })).toBeVisible()

  const memberContext = await browser.newContext()
  const researchContext = await browser.newContext()
  const harborContext = await browser.newContext()
  try {
    const member = await memberContext.newPage()
    await login(member, "member@northstar.example")
    const permitted = await query(member, "What is the leave policy?")
    expect(permitted.status).toBe("answered")
    expect(permitted.citations.length).toBeGreaterThan(0)
    expect(
      permitted.citations.every(
        (citation) =>
          citation.filename === "journey-policy.txt" &&
          citation.location.length > 0,
      ),
    ).toBe(true)
    await expect(member.getByRole("list", { name: "Citations" })).toContainText(
      "journey-policy.txt",
    )
    const permittedIds = permitted.citations.map(
      (citation) => citation.chunk_id,
    )
    const permittedTrace = await trace(member)
    expect(
      permittedTrace.stages.some((stage) =>
        stage.chunk_ids.some((id) => permittedIds.includes(id)),
      ),
    ).toBe(true)

    const research = await researchContext.newPage()
    await login(research, "research@northstar.example")
    const denied = await query(research, "What is the leave policy?")
    expect(denied.status).toBe("abstained")
    expect(denied.citations).toEqual([])
    await expect(
      research.getByText("No supporting evidence was found", { exact: true }),
    ).toBeVisible()
    const deniedTrace = await trace(research)
    expect(deniedTrace.stages.flatMap((stage) => stage.chunk_ids)).toEqual([])
    expect(JSON.stringify([denied, deniedTrace])).not.toContain(
      "PRIVATE_NORTHSTAR_JOURNEY",
    )
    for (const id of permittedIds)
      expect(JSON.stringify([denied, deniedTrace])).not.toContain(id)

    const harbor = await harborContext.newPage()
    await login(harbor, "owner@harbor.example", "harbor-works")
    const foreign = await query(harbor, "What is the leave policy?")
    expect(foreign.status).toBe("abstained")
    expect(foreign.citations).toEqual([])
    const foreignTrace = await trace(harbor)
    expect(foreignTrace.stages.flatMap((stage) => stage.chunk_ids)).toEqual([])
    for (const id of permittedIds)
      expect(JSON.stringify([foreign, foreignTrace])).not.toContain(id)

    await upload(
      harbor,
      "harbor-policy.txt",
      "Harbor leave policy permits ten days. PRIVATE_HARBOR_JOURNEY.",
    )
    await harbor
      .getByRole("button", { name: "Edit access", exact: true })
      .click()
    await harbor
      .getByRole("radio", { name: "Organization-wide", exact: true })
      .check()
    await harbor
      .getByRole("button", { name: "Save access", exact: true })
      .click()
    await expect(
      harbor.getByText("Access saved", { exact: true }),
    ).toBeVisible()
    const ownTenant = await query(harbor, "What is the leave policy?")
    expect(ownTenant.status).toBe("answered")
    expect(
      ownTenant.citations.every(
        (citation) => citation.filename === "harbor-policy.txt",
      ),
    ).toBe(true)
    expect(ownTenant.citations.length).toBeGreaterThan(0)
    const ownTrace = await trace(harbor)
    for (const id of permittedIds)
      expect(JSON.stringify([ownTenant, ownTrace])).not.toContain(id)

    await page.goto(documentUrl)
    await page.getByRole("button", { name: "Edit access", exact: true }).click()
    await page
      .getByRole("checkbox", { name: "JourneyAllowed", exact: true })
      .uncheck()
    await page.getByRole("checkbox", { name: "Research", exact: true }).check()
    await page.getByRole("button", { name: "Save access", exact: true }).click()
    await expect(page.getByText("Access saved", { exact: true })).toBeVisible()
    const revoked = await query(member, "What is the leave policy now?")
    expect(revoked.status).toBe("abstained")
    expect(revoked.citations).toEqual([])
    const revokedTrace = await trace(member)
    expect(revokedTrace.stages.flatMap((stage) => stage.chunk_ids)).toEqual([])
    for (const id of permittedIds)
      expect(JSON.stringify([revoked, revokedTrace])).not.toContain(id)
    const sameRolePositive = await query(
      research,
      "What is the leave policy now?",
    )
    expect(sameRolePositive.status).toBe("answered")
    expect(sameRolePositive.citations.length).toBeGreaterThan(0)
    expect(
      sameRolePositive.citations.every(
        (citation) => citation.filename === "journey-policy.txt",
      ),
    ).toBe(true)
    await expect(
      research.getByRole("list", { name: "Citations" }),
    ).toContainText("journey-policy.txt")
  } finally {
    await memberContext.close()
    await researchContext.close()
    await harborContext.close()
  }
})
