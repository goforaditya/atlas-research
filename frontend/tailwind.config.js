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
        chip: "var(--chip)",
        ps: "var(--ps)",
        ps2: "var(--ps2)",
        ng: "var(--ng)",
        ng2: "var(--ng2)",
        wn: "var(--wn)",
        wn2: "var(--wn2)",
      },
      fontFamily: {
        serif: ["inherit"],
        sans: ["inherit"],
        mono: ["ui-monospace", "'SF Mono'", "Consolas", "Menlo", "monospace"],
      },
      fontSize: {
        micro: ["13px", { lineHeight: "1.35", fontWeight: "500" }],
        dense: ["14px", "1.5"],
        body: ["16px", "1.55"],
        h3: ["17px", { lineHeight: "1.35", fontWeight: "600" }],
        h2: ["24px", { lineHeight: "1.25", fontWeight: "650" }],
        h1: ["34px", { lineHeight: "1.15", fontWeight: "700", letterSpacing: "-0.03em" }],
        kpi: ["28px", { lineHeight: "1.15", fontWeight: "650" }],
      },
      maxWidth: { report: "1040px", explain: "1200px" },
      borderRadius: { card: "16px" },
      transitionTimingFunction: { out: "cubic-bezier(0.22, 1, 0.36, 1)" },
    },
  },
  plugins: [],
};
