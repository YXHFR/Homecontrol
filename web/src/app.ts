/** Ritar upp webbgränssnittet. Än så länge en tom sida. */
export function mountApp(root: HTMLElement): void {
  root.replaceChildren();
  root.dataset.ready = "true";
}
