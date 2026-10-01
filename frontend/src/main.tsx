import { RouterProvider } from "@tanstack/react-router"
import { createRoot } from "react-dom/client"

import { AuthProvider } from "./features/auth/AuthProvider"
import { router } from "./router"
import "./styles.css"

const root = document.getElementById("root")
if (!root) throw new Error("Missing application root")

createRoot(root).render(
  <AuthProvider>
    <RouterProvider router={router} />
  </AuthProvider>,
)
