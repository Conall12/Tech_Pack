// Light is the default. The choice is remembered in this browser.
(() => {
  const root = document.documentElement;
  const save = v => { try { localStorage.setItem("selvedge-theme", v); } catch (e) {} };
  document.querySelectorAll(".theme-toggle").forEach(btn => {
    const paint = () => btn.setAttribute("aria-pressed", root.dataset.theme === "dark" ? "true" : "false");
    paint();
    btn.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      if (next === "dark") root.dataset.theme = "dark"; else delete root.dataset.theme;
      save(next);
      document.querySelectorAll(".theme-toggle").forEach(b => b.setAttribute("aria-pressed", next === "dark" ? "true" : "false"));
    });
  });
})();
