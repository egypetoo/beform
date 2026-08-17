if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

const installButton = document.getElementById("pwa-install");
const iosHint = document.getElementById("pwa-ios-hint");
let deferredPrompt = null;

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredPrompt = event;
  if (installButton) {
    installButton.classList.remove("hidden");
  }
});

if (installButton) {
  installButton.addEventListener("click", async () => {
    if (!deferredPrompt) {
      return;
    }
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    installButton.classList.add("hidden");
  });
}

window.addEventListener("appinstalled", () => {
  if (installButton) {
    installButton.classList.add("hidden");
  }
  if (iosHint) {
    iosHint.classList.add("hidden");
  }
});

const isStandalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
const isIOS = /iphone|ipad|ipod/i.test(window.navigator.userAgent);
if (iosHint && isIOS && !isStandalone) {
  iosHint.classList.remove("hidden");
}
