/**
 * Synchronises aria-invalid, dynamic aria-describedby error linking, and polite live announcements
 * after interaction per modern-web-guidance `validate-input-after-interaction`.
 */

const flagged = new WeakSet<HTMLElement>();
const announced = new WeakSet<HTMLElement>();
let timer: ReturnType<typeof setTimeout> | undefined;
let lastAnnouncedEl: HTMLElement | undefined;

export function syncFieldAria(
  el: EventTarget | null,
  announcer: HTMLElement | null,
  announce = false
): void {
  if (!(el instanceof HTMLElement)) return;
  if (!el.matches("input, select, textarea")) return;

  const control = el as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
  let matchesUserInvalid = false;
  try {
    matchesUserInvalid = control.matches(":user-invalid");
  } catch {
    // Fallback for test environments (e.g. jsdom) that lack :user-invalid support.
    matchesUserInvalid =
      control.classList.contains("user-invalid-fallback") ||
      (control.dataset.interacted === "true" && !control.validity.valid);
  }

  const linkError = (shown: boolean) => {
    const errId = control.dataset.error;
    if (!errId) return;
    const ids = (control.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((item) => item && item !== errId);
    if (shown) ids.push(errId);
    if (ids.length > 0) control.setAttribute("aria-describedby", ids.join(" "));
    else control.removeAttribute("aria-describedby");
  };

  if (matchesUserInvalid) {
    if (announce && announcer && !announced.has(control) && control.dataset.error) {
      const errNode = document.getElementById(control.dataset.error);
      if (errNode) {
        const labelText = control.labels?.[0]?.textContent?.trim();
        const msgText = errNode.textContent?.trim() || "";
        if (timer) clearTimeout(timer);
        announcer.textContent = "";
        lastAnnouncedEl = control;
        timer = setTimeout(() => {
          announcer.textContent = labelText ? `${labelText}: ${msgText}` : msgText;
        }, 120);
        announced.add(control);
      }
    }
    control.setAttribute("aria-invalid", "true");
    linkError(true);
    flagged.add(control);
  } else if (flagged.has(control) || control.getAttribute("aria-invalid") !== "true") {
    control.setAttribute("aria-invalid", "false");
    linkError(false);
    flagged.delete(control);
    announced.delete(control);
    if (lastAnnouncedEl === control && announcer) {
      if (timer) clearTimeout(timer);
      announcer.textContent = "";
    }
  }
}

export function attachAriaValidationSync(
  root: HTMLElement,
  announcer: HTMLElement | null
): () => void {
  root.querySelectorAll("input, select, textarea").forEach((el) => {
    if (!el.hasAttribute("aria-invalid")) {
      el.setAttribute("aria-invalid", "false");
    }
  });

  const onBlur = (e: Event) => {
    if (e.target instanceof HTMLElement) {
      e.target.dataset.interacted = "true";
      const control = e.target as HTMLInputElement;
      if ("validity" in control && !control.validity.valid) {
        control.classList.add("user-invalid-fallback");
      } else {
        control.classList.remove("user-invalid-fallback");
      }
      syncFieldAria(e.target, announcer, true);
    }
  };

  const onInput = (e: Event) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      if (e.target.classList.contains("user-invalid-fallback") && e.target.validity.valid) {
        e.target.classList.remove("user-invalid-fallback");
      }
      syncFieldAria(e.target, announcer, false);
    }
  };

  const onChange = (e: Event) => {
    if (e.target instanceof HTMLElement) {
      e.target.dataset.interacted = "true";
      syncFieldAria(e.target, announcer, true);
    }
  };

  const onInvalid = (e: Event) => {
    if (e.target instanceof HTMLElement) {
      e.target.dataset.interacted = "true";
      e.target.classList.add("user-invalid-fallback");
      announced.add(e.target);
      syncFieldAria(e.target, announcer, false);
    }
  };

  root.addEventListener("blur", onBlur, true);
  root.addEventListener("input", onInput, true);
  root.addEventListener("change", onChange, true);
  root.addEventListener("invalid", onInvalid, true);

  return () => {
    root.removeEventListener("blur", onBlur, true);
    root.removeEventListener("input", onInput, true);
    root.removeEventListener("change", onChange, true);
    root.removeEventListener("invalid", onInvalid, true);
  };
}
