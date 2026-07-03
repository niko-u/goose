// LabVault front-end: marker time-series chart, sparklines, upload dropzone.
// All data comes from the local /api endpoints — no external requests.

(function () {
  "use strict";

  function themeColors() {
    const cs = getComputedStyle(document.documentElement);
    return {
      text: cs.getPropertyValue("--text").trim() || "#e6edf3",
      muted: cs.getPropertyValue("--muted").trim() || "#8b98a8",
      accent: cs.getPropertyValue("--accent").trim() || "#4aa8ff",
      border: cs.getPropertyValue("--border").trim() || "#2c3644",
      high: cs.getPropertyValue("--high").trim() || "#f85149",
    };
  }

  async function fetchSeries(markerId) {
    const res = await fetch(`/api/marker/${markerId}`);
    if (!res.ok) throw new Error("failed to load series");
    return res.json();
  }

  function renderMainChart() {
    const canvas = document.getElementById("chart");
    if (!canvas || !window.LABVAULT_MARKER_ID || !window.Chart) return;
    const c = themeColors();
    fetchSeries(window.LABVAULT_MARKER_ID).then((data) => {
      const pts = data.series.filter((s) => s.value !== null && s.value !== undefined);
      const warn = document.getElementById("chart-warn");
      if (pts.length === 0) {
        if (warn) warn.textContent = "No numeric values to plot (qualitative results only).";
        return;
      }
      const units = new Set(pts.map((p) => p.unit).filter(Boolean));
      if (warn && units.size > 1) {
        warn.textContent = "⚠ Values span multiple units that could not all be converted: " +
          [...units].join(", ") + ". Plot may mix scales.";
      }
      const labels = pts.map((p) => p.collected_at);
      const values = pts.map((p) => p.value);
      const refLow = pts.map((p) => p.ref_low);
      const refHigh = pts.map((p) => p.ref_high);
      const unit = pts[pts.length - 1].unit || "";

      const datasets = [{
        label: (data.marker.canonical_name || "Value") + (unit ? ` (${unit})` : ""),
        data: values,
        borderColor: c.accent,
        backgroundColor: c.accent,
        tension: 0.2,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointBackgroundColor: pts.map((p) => (p.flag ? c.high : c.accent)),
      }];
      if (refHigh.some((v) => v !== null)) {
        datasets.push({ label: "Ref high", data: refHigh, borderColor: c.muted, borderDash: [5, 5], pointRadius: 0, fill: false });
      }
      if (refLow.some((v) => v !== null)) {
        datasets.push({ label: "Ref low", data: refLow, borderColor: c.muted, borderDash: [5, 5], pointRadius: 0, fill: false });
      }

      new Chart(canvas, {
        type: "line",
        data: { labels, datasets },
        options: {
          responsive: true,
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { labels: { color: c.text } } },
          scales: {
            x: { ticks: { color: c.muted }, grid: { color: c.border } },
            y: { ticks: { color: c.muted }, grid: { color: c.border } },
          },
        },
      });
    }).catch(() => {});
  }

  function renderSparklines() {
    if (!window.Chart) return;
    const c = themeColors();
    document.querySelectorAll(".spark[data-marker]").forEach((el) => {
      const id = el.getAttribute("data-marker");
      const canvas = document.createElement("canvas");
      el.appendChild(canvas);
      fetchSeries(id).then((data) => {
        const pts = data.series.filter((s) => typeof s.value === "number");
        if (pts.length < 2) { el.innerHTML = '<span class="muted small">—</span>'; return; }
        new Chart(canvas, {
          type: "line",
          data: {
            labels: pts.map((p) => p.collected_at),
            datasets: [{ data: pts.map((p) => p.value), borderColor: c.accent, pointRadius: 0, tension: 0.3, borderWidth: 1.5 }],
          },
          options: {
            responsive: false, animation: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false } },
            elements: { line: { fill: false } },
          },
        });
      }).catch(() => { el.innerHTML = ""; });
    });
  }

  function setupDropzone() {
    const form = document.getElementById("dropform");
    if (!form) return;
    const input = document.getElementById("fileinput");
    const inner = document.getElementById("dzinner");
    const list = document.getElementById("filelist");

    inner.addEventListener("click", () => input.click());
    input.addEventListener("change", () => showFiles(input.files));
    ["dragover", "dragenter"].forEach((ev) =>
      inner.addEventListener(ev, (e) => { e.preventDefault(); inner.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      inner.addEventListener(ev, (e) => { e.preventDefault(); inner.classList.remove("drag"); }));
    inner.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        showFiles(input.files);
      }
    });
    function showFiles(files) {
      if (!files || !files.length) { list.textContent = ""; return; }
      list.textContent = [...files].map((f) => f.name).join(", ");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    renderMainChart();
    renderSparklines();
    setupDropzone();
  });
})();
