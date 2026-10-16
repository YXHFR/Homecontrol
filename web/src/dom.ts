/** Skapar ett HTML-element med egenskaper och barn. Text blir alltid text, aldrig HTML. */
export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

/** En vanlig knapp som gör `onClick` när den trycks. */
export function button(label: string, onClick: () => void, className = ""): HTMLButtonElement {
  const node = element("button", { type: "button", className }, label);
  node.addEventListener("click", onClick);
  return node;
}
