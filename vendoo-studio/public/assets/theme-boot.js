// Match the workspace theme before the first paint, even on a slow connection.
// A file rather than an inline script: the hosted page's policy allows only scripts from its own origin.
(function () {
  try {
    var pref = localStorage.getItem("vendoo-studio:theme");
    var light = pref === "light" || (pref === "system" && matchMedia("(prefers-color-scheme: light)").matches);
    document.documentElement.classList.toggle("dark", !light);
    document.documentElement.style.colorScheme = light ? "light" : "dark";
    document.querySelector('meta[name="color-scheme"]').setAttribute("content", light ? "light" : "dark");
  } catch (e) {}
})();
