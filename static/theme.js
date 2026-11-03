(function () {
    var STORAGE_KEY = "be-theme";
    var root = document.documentElement;
    var meta = document.querySelector('meta[name="theme-color"]');

    function preferredTheme() {
        try {
            var saved = localStorage.getItem(STORAGE_KEY);
            if (saved === "light" || saved === "dark") return saved;
        } catch (err) {}
        if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
            return "light";
        }
        return "dark";
    }

    function applyTheme(theme) {
        var next = theme === "light" ? "light" : "dark";
        root.setAttribute("data-theme", next);
        if (meta) {
            meta.setAttribute("content", next === "light" ? "#f4f1ec" : "#070707");
        }
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch (err) {}
        document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
            button.setAttribute("aria-pressed", next === "dark" ? "true" : "false");
            button.setAttribute(
                "aria-label",
                next === "dark" ? "Switch to light mode" : "Switch to dark mode"
            );
        });
    }

    function toggleTheme() {
        applyTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light");
    }

    applyTheme(preferredTheme());

    document.addEventListener("click", function (event) {
        var button = event.target.closest("[data-theme-toggle]");
        if (!button) return;
        event.preventDefault();
        toggleTheme();
    });

    if (window.matchMedia) {
        var media = window.matchMedia("(prefers-color-scheme: light)");
        var onChange = function () {
            try {
                if (localStorage.getItem(STORAGE_KEY)) return;
            } catch (err) {}
            applyTheme(preferredTheme());
        };
        if (media.addEventListener) media.addEventListener("change", onChange);
        else if (media.addListener) media.addListener(onChange);
    }
})();
