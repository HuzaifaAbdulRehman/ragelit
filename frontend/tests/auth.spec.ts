import { expect, type Page, test } from "@playwright/test"

const ownerEmail = "owner@northstar.example"
const demoPassword = "task-nine-test-password"

async function openLogin(page: Page) {
  await page.goto("http://127.0.0.1:5173/login")
}

test("invalid credentials show a non-enumerating error", async ({ page }) => {
  await openLogin(page)
  await page.getByLabel("Work email").fill(ownerEmail)
  await page.getByLabel("Password").fill("wrong-password")
  await page.getByLabel("Organization").fill("northstar-labs")
  await page.getByRole("button", { name: "Sign in" }).click()

  await expect(page.getByRole("alert")).toHaveText("Authentication failed.")
})

test("owner can sign in, view organizations, and log out", async ({ page }) => {
  await openLogin(page)
  await page.getByLabel("Work email").fill(ownerEmail)
  await page.getByLabel("Password").fill(demoPassword)
  await page.getByLabel("Organization").fill("northstar-labs")
  await page.getByRole("button", { name: "Sign in" }).click()

  await expect(
    page.getByRole("heading", { name: "Northstar Labs" }),
  ).toBeVisible()
  await expect(page.getByLabel("Organization switcher")).toContainText(
    "Harbor Works",
  )
  await expect(page.getByRole("navigation")).toContainText("People")

  await page.getByLabel("Organization switcher").selectOption({
    label: "Harbor Works",
  })
  await expect(
    page.getByRole("heading", { name: "Harbor Works" }),
  ).toBeVisible()
  await expect(page.getByRole("navigation")).not.toContainText("People")

  await page.getByRole("button", { name: "Log out" }).click()
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible()
})

test("cookie refresh restores the in-memory session after reload", async ({
  page,
}) => {
  await openLogin(page)
  await page.getByLabel("Work email").fill(ownerEmail)
  await page.getByLabel("Password").fill(demoPassword)
  await page.getByLabel("Organization").fill("northstar-labs")
  await page.getByRole("button", { name: "Sign in" }).click()
  await expect(
    page.getByRole("heading", { name: "Northstar Labs" }),
  ).toBeVisible()

  await page.reload()

  await expect(
    page.getByRole("heading", { name: "Northstar Labs" }),
  ).toBeVisible()
})
