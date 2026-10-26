/** Design system tokens (Section 9.1). Colours resolve to CSS variables so both themes share one palette. */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        sf: "var(--sf)",
        "sf-elev": "var(--sf-elev)",
        tx: "var(--tx)",
        mu: "var(--mu)",
        ln: "var(--ln)",
        ac: "var(--ac)",
        ac2: "var(--ac2)",
        ps: "var(--ps)",
        ps2: "var(--ps2)",
        ng: "var(--ng)",
        ng2: "var(--ng2)",
        wn: "var(--wn)",
        wn2: "var(--wn2)",
      },
      fontFamily: {
        serif: ["Newsreader", "Georgia", "serif"],
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "Menlo", "monospace"],
      },
      fontSize: {
        micro: ["12px", { lineHeight: "1.3", fontWeight: "500" }],
        dense: ["13px", "1.45"],
        body: ["15px", "1.5"],
        h3: ["16px", { lineHeight: "1.35", fontWeight: "600" }],
        h2: ["21px", { lineHeight: "1.3", fontWeight: "600" }],
        h1: ["26px", { lineHeight: "1.25", fontWeight: "600", letterSpacing: "-0.01em" }],
        kpi: ["22px", { lineHeight: "1.2", fontWeight: "600" }],
      },
      maxWidth: { report: "980px", explain: "1180px" },
      borderRadius: { card: "10px" },
      transitionTimingFunction: { out: "cubic-bezier(0.22, 1, 0.36, 1)" },
    },
  },
  plugins: [],
};
