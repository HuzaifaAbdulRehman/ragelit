import { expect, test } from "@playwright/test"

test("manual navigation to another organization hides whether it exists", async ({
  page,
}) => {
  await page.goto("http://127.0.0.1:5173/login")
  await page.getByLabel("Work email").fill("owner@northstar.example")
  await page.getByLabel("Password").fill("task-nine-test-password")
  await page.getByLabel("Organization").fill("northstar-labs")
  await page.getByRole("button", { name: "Sign in" }).click()

  const otherOrganizationId = await page
    .getByLabel("Organization switcher")
    .locator("option", { hasText: "Harbor Works" })
    .getAttribute("value")
  expect(otherOrganizationId).toBeTruthy()

  await page.goto(
    `http://127.0.0.1:5173/organizations/${otherOrganizationId}/members`,
  )

  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible()
  await expect(
    page.getByText("The requested resource was not found."),
  ).toBeVisible()
  await expect(page.getByText("Harbor Works")).not.toBeVisible()
})
