import { mountApp } from "./app";

const root = document.getElementById("app");
if (root === null) {
  throw new Error("Hittade inget #app-element att rita i");
}
mountApp(root);
