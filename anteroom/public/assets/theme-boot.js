// Match the workspace theme before the first paint, even on a slow connection.
// A file rather than an inline script: the hosted page's policy allows only scripts from its own origin.
(function () {
  try {
    var pref = localStorage.getItem("anteroom:theme") || localStorage.getItem("vendoo-studio:theme");
    if (pref !== "light" && pref !== "dark") pref = "system";
    var light = pref === "light" || (pref === "system" && matchMedia("(prefers-color-scheme: light)").matches);
    document.documentElement.classList.toggle("dark", !light);
    document.documentElement.style.colorScheme = light ? "light" : "dark";
    var themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) themeMeta.setAttribute("content", light ? "#ffffff" : "#000000");
    var schemeMeta = document.querySelector('meta[name="color-scheme"]');
    if (schemeMeta) schemeMeta.setAttribute("content", light ? "light" : "dark");
  } catch (e) {}
})();

// The application cannot report errors or offer a reload until its modules run.
// Both static shells use the same ID. Keep it stable because the HTML and script
// can come from different builds during a rollout.
(function () {
  function showRecovery(failed) {
    var shell = document.getElementById("cloud-startup");
    if (!shell) return;
    var status = shell.querySelector(".startup-status");
    var reload = shell.querySelector(".startup-reload");
    if (failed) {
      status.setAttribute("role", "alert");
      status.querySelector(".startup-spinner").hidden = true;
    }
    status.querySelector("p").textContent = failed
      ? "Could not load Anteroom. Reload to try again."
      : "Taking longer than usual. You can keep waiting or reload.";
    reload.onclick = function () { window.location.reload(); };
    reload.hidden = false;
  }

  var slow = window.setTimeout(function () { showRecovery(false); }, 10000);
  window.addEventListener("error", function (event) {
    if (event.target instanceof HTMLScriptElement && event.target.type === "module") {
      window.clearTimeout(slow);
      showRecovery(true);
    }
  }, true);
})();
