import { expect, type Page, test } from "@playwright/test"
import type { components } from "../src/api/generated/schema"
import {
  document,
  documentId,
  groupId,
  member,
  mockSession,
  orgA,
  problem,
  signIn,
  userId,
} from "./helpers/session"

async function editor(
  page: Page,
  initial: components["schemas"]["AccessResponse"],
) {
  await mockSession(page)
  const state = {
    access: initial,
    command: null as components["schemas"]["AccessCommand"] | null,
    fail: false,
    readFail: false,
    writes: 0,
  }
  await page.route("**/api/v1/documents**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() === "PATCH") {
      state.writes++
      state.command = request.postDataJSON()
      if (state.fail) {
        await route.fulfill({
          status: 503,
          json: problem(
            "projection_unavailable",
            503,
            "Access projection unavailable.",
          ),
        })
        return
      }
      state.access = {
        visibility: state.command?.visibility ?? "restricted",
        user_ids: state.command?.user_ids ?? [],
        group_ids: state.command?.group_ids ?? [],
      }
      await route.fulfill({
        json: { ...document, visibility: state.access.visibility },
      })
    } else if (path.endsWith("/access")) {
      await route.fulfill(
        state.readFail
          ? {
              status: 503,
              json: problem("unavailable", 503, "Access cannot be loaded."),
            }
          : { json: state.access },
      )
    } else if (path.endsWith("/versions")) await route.fulfill({ json: [] })
    else if (path.endsWith(documentId))
      await route.fulfill({
        json: { ...document, visibility: state.access.visibility },
      })
    else await route.fulfill({ json: [document] })
  })
  await signIn(page)
  await page.getByRole("link", { name: "Documents", exact: true }).click()
  await page.getByRole("link", { name: document.filename, exact: true }).click()
  await page.getByRole("button", { name: "Edit access", exact: true }).click()
  return state
}

test("existing direct and group grants reopen and use user IDs", async ({
  page,
}) => {
  const state = await editor(page, {
    visibility: "restricted",
    user_ids: [userId],
    group_ids: [groupId],
  })
  await expect(
    page.getByRole("checkbox", { name: member.email, exact: true }),
  ).toBeChecked()
  await expect(
    page.getByRole("checkbox", { name: "Engineering", exact: true }),
  ).toBeChecked()
  await page
    .getByRole("checkbox", { name: member.email, exact: true })
    .uncheck()
  await page.getByRole("button", { name: "Save access", exact: true }).click()
  await expect(page.getByText("Access saved", { exact: true })).toBeVisible()
  expect(state.command).toEqual({
    visibility: "restricted",
    user_ids: [],
    group_ids: [groupId],
  })
  await page.getByRole("button", { name: "Edit access", exact: true }).click()
  await expect(
    page.getByRole("checkbox", { name: member.email, exact: true }),
  ).not.toBeChecked()
  await expect(
    page.getByRole("checkbox", { name: "Engineering", exact: true }),
  ).toBeChecked()
  await page.getByRole("checkbox", { name: member.email, exact: true }).check()
  await page.getByRole("button", { name: "Save access", exact: true }).click()
  await expect(page.getByText("Access saved", { exact: true })).toBeVisible()
  expect(state.command?.user_ids).toEqual([userId])
  expect(state.command?.user_ids).not.toContain(member.id)
})

test("saving preserves grants outside the first picker page", async ({
  page,
}) => {
  const existingUnlistedUserId = "99999999-9999-4999-8999-999999999999"
  await mockSession(page)
  const state = await editor(page, {
    visibility: "restricted",
    user_ids: [existingUnlistedUserId],
    group_ids: [],
  })
  await page.route(`**/api/v1/organizations/${orgA}/members*`, (route) => {
    const offset = Number(
      new URL(route.request().url()).searchParams.get("offset") ?? 0,
    )
    return route.fulfill({
      json: {
        items:
          offset === 0
            ? Array.from({ length: 50 }, (_, i) => ({
                ...member,
                id: `m${i}`,
                user_id: `u${i}`,
                email: `page${i}@example.com`,
              }))
            : [
                {
                  ...member,
                  user_id: existingUnlistedUserId,
                  email: "existing@example.com",
                },
              ],
        limit: 50,
        offset,
      },
    })
  })
  await page.getByRole("button", { name: "Cancel access", exact: true }).click()
  await page.getByRole("button", { name: "Edit access", exact: true }).click()
  await expect(
    page.getByRole("button", {
      name: `Remove user ${existingUnlistedUserId}`,
      exact: true,
    }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Next users", exact: true }).click()
  await expect(
    page.getByRole("checkbox", { name: "existing@example.com", exact: true }),
  ).toBeChecked()
  await page
    .getByRole("button", { name: "Previous users", exact: true })
    .click()
  await page.getByRole("checkbox", { name: "Engineering", exact: true }).check()
  await page.getByRole("button", { name: "Save access", exact: true }).click()
  await expect(page.getByText("Access saved", { exact: true })).toBeVisible()
  expect(state.command?.user_ids).toContain(existingUnlistedUserId)
})

test("projection failure keeps persisted access separate from the draft", async ({
  page,
}) => {
  const state = await editor(page, {
    visibility: "restricted",
    user_ids: [userId],
    group_ids: [groupId],
  })
  state.fail = true
  await page
    .getByRole("radio", { name: "Organization-wide", exact: true })
    .check()
  await page.getByRole("button", { name: "Save access", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("Access was not saved")
  await expect(page.getByText("Access saved", { exact: true })).toHaveCount(0)
  await expect(
    page.getByText("Persisted access: Restricted", { exact: false }),
  ).toBeVisible()
  expect(state.access.visibility).toBe("restricted")
  state.fail = false
  await page
    .getByRole("button", { name: "Retry save access", exact: true })
    .click()
  await expect(page.getByText("Access saved", { exact: true })).toBeVisible()
  expect(state.command).toEqual({
    visibility: "organization",
    user_ids: [],
    group_ids: [],
  })
})

test("cancelling discards changes without changing persisted grants", async ({
  page,
}) => {
  const state = await editor(page, {
    visibility: "restricted",
    user_ids: [userId],
    group_ids: [],
  })
  await page
    .getByRole("checkbox", { name: member.email, exact: true })
    .uncheck()
  await page.getByRole("checkbox", { name: "Engineering", exact: true }).check()
  await page.getByRole("button", { name: "Cancel access", exact: true }).click()
  expect(state.writes).toBe(0)
  await page.getByRole("button", { name: "Edit access", exact: true }).click()
  await expect(
    page.getByRole("checkbox", { name: member.email, exact: true }),
  ).toBeChecked()
  await expect(
    page.getByRole("checkbox", { name: "Engineering", exact: true }),
  ).not.toBeChecked()
})

test("failed grant loading cannot enable a default-empty save", async ({
  page,
}) => {
  const state = await editor(page, {
    visibility: "restricted",
    user_ids: [userId],
    group_ids: [],
  })
  await page.getByRole("button", { name: "Cancel access", exact: true }).click()
  state.readFail = true
  await page.getByRole("button", { name: "Edit access", exact: true }).click()
  await expect(page.getByRole("alert")).toContainText("Access cannot be loaded")
  await expect(
    page.getByRole("button", { name: "Save access", exact: true }),
  ).toBeDisabled()
  expect(state.writes).toBe(0)
})
