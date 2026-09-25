// ===============================
// MENU HAMBURGUESA (MOBILE)
// ===============================
(function () {
  const toggle = document.getElementById("gpMenuToggle");
  const mobileNav = document.getElementById("gpMobileNav");

  if (!toggle || !mobileNav) return;

  toggle.addEventListener("click", function () {
    const isOpen = mobileNav.classList.toggle("gp-nav-mobile--open");
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    toggle.classList.toggle("is-open", isOpen);
  });
})();


// ===============================
// CARROUSEL DE TESTIMONIOS
// ===============================
(function () {
  const testimonials = [
    { text: "“Súper práctico para organizar partidos y no perder el score.”", name: "Juan Pérez", cat: "Categoría 8va", stars: 5 },
    { text: "“Reservé turno en minutos. Muy clara la experiencia.”", name: "María Gómez", cat: "Categoría 6ta", stars: 5 },
    { text: "“Me encantó poder guardar estadísticas del partido.”", name: "Lucas Fernández", cat: "Categoría 4ta", stars: 4 }
  ];

  const textEl = document.getElementById("gpTestText");
  const nameEl = document.getElementById("gpTestName");
  const catEl = document.getElementById("gpTestCat");
  const starsEl = document.getElementById("gpTestStars");
  const prevBtn = document.getElementById("gpTestPrev");
  const nextBtn = document.getElementById("gpTestNext");

  if (!textEl || !nameEl || !catEl || !starsEl || !prevBtn || !nextBtn) return;

  let current = 0;

  function render() {
    const t = testimonials[current];
    textEl.textContent = t.text;
    nameEl.textContent = t.name;
    catEl.textContent = t.cat;
    starsEl.textContent = "★★★★★".slice(0, t.stars);
  }

  prevBtn.addEventListener("click", () => {
    current = (current - 1 + testimonials.length) % testimonials.length;
    render();
  });

  nextBtn.addEventListener("click", () => {
    current = (current + 1) % testimonials.length;
    render();
  });

  render();
})();


// ===============================
// SEARCH AIRBNB – ABRIR / CERRAR
// ===============================
(function () {
  const items = document.querySelectorAll(".gp-search-item");
  if (!items.length) return;

  items.forEach(item => {
    item.addEventListener("click", e => {
      e.stopPropagation();
      items.forEach(i => i.classList.remove("active"));
      item.classList.add("active");
    });
  });

  document.addEventListener("click", () => {
    items.forEach(i => i.classList.remove("active"));
  });
})();


// ===============================
// CIUDAD (LIBRE + SUGERENCIAS)
// ===============================
(function () {
  const cityInput = document.getElementById("cityInput");
  const cityList = document.getElementById("citySuggestions");
  const cityValue = document.getElementById("cityValue");

  if (!cityInput || !cityList || !cityValue) return;

  const cities = [
    "Buenos Aires",
    "La Plata",
    "Mar del Plata",
    "Tandil",
    "Bahía Blanca",
    "San Cayetano"
  ];

  function closePanels() {
    document.querySelectorAll(".gp-search-item").forEach(i => i.classList.remove("active"));
  }

  function setCity(value) {
    const v = (value || "").trim();
    if (!v) return;

    cityValue.textContent = v;
    cityInput.value = v;
    cityList.innerHTML = "";
    closePanels();
  }

  function renderSuggestions(query) {
    cityList.innerHTML = "";
    const q = (query || "").trim().toLowerCase();
    if (!q) return;

    cities
      .filter(c => c.toLowerCase().includes(q))
      .slice(0, 8)
      .forEach(city => {
        const li = document.createElement("li");
        li.textContent = city;
        li.addEventListener("click", () => setCity(city));
        cityList.appendChild(li);
      });
  }

  cityInput.addEventListener("input", () => renderSuggestions(cityInput.value));

  cityInput.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      setCity(cityInput.value);
    }
  });

  cityInput.addEventListener("blur", () => {
    setTimeout(() => {
      if (cityInput.value.trim()) setCity(cityInput.value);
    }, 120);
  });
})();


// ===============================
// FECHA – FLATPICKR
// ===============================
(function () {
  if (!window.flatpickr) return;

  if (window.flatpickr.l10ns?.es) {
    window.flatpickr.localize(window.flatpickr.l10ns.es);
  }

  const dateInput = document.getElementById("dateInput");
  const dateValue = document.getElementById("dateValue");

  if (!dateInput || !dateValue) return;

  flatpickr(dateInput, {
    dateFormat: "Y-m-d",
    disableMobile: true,
    onChange: function (selectedDates, dateStr) {
      dateValue.textContent = dateStr || "¿Qué día?";
    }
  });
})();


// ===============================
// HORARIOS – FLATPICKR SOLO 00 / 30
// ===============================
(function () {
  // ✅ Si la página no usa flatpickr, salimos y NO rompemos el resto del main.js
  if (!window.flatpickr) return;

  function initTimePickers(context = document) {
    context.querySelectorAll(".fp-time").forEach((input) => {
      if (input._flatpickr) return;

      const panel = input.closest(".gp-search-panel") || document.body;

      window.flatpickr(input, {
        enableTime: true,
        noCalendar: true,
        dateFormat: "H:i",
        time_24hr: true,
        minuteIncrement: 30,
        disableMobile: true,
        appendTo: panel,
        position: "below left",
      });
    });
  }

  initTimePickers();

  const addBtn = document.querySelector(".gp-add-time");
  const timePanel = document.getElementById("timePanel");

  if (addBtn && timePanel) {
    addBtn.addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "gp-time-row";
      row.innerHTML = `
        <input type="text" class="gp-time-input fp-time" placeholder="Hora inicio">
        <span>–</span>
        <input type="text" class="gp-time-input fp-time" placeholder="Hora fin">
      `;
      timePanel.prepend(row);
      initTimePickers(row);
    });
  }
})();


// ===============================
// SEARCH MOBILE: abrir/cerrar panel
// ===============================
(function () {
  const openBtn = document.getElementById("gpSearchOpen");
  const closeBtn = document.getElementById("gpSearchClose");
  const expand = document.getElementById("gpSearchExpand");

  if (!openBtn || !closeBtn || !expand) return;

  function open() {
    expand.classList.add("is-open");
    expand.setAttribute("aria-hidden", "false");
  }

  function close() {
    expand.classList.remove("is-open");
    expand.setAttribute("aria-hidden", "true");
    document.querySelectorAll(".gp-search-item").forEach(i => i.classList.remove("active"));
  }

  openBtn.addEventListener("click", (e) => {
    e.preventDefault();
    open();
  });

  closeBtn.addEventListener("click", (e) => {
    e.preventDefault();
    close();
  });

  // click afuera cierra (solo mobile)
  document.addEventListener("click", (e) => {
    if (window.innerWidth > 800) return;
    if (!expand.classList.contains("is-open")) return;
    const clickedInside = expand.contains(e.target) || openBtn.contains(e.target);
    if (!clickedInside) close();
  });
})();

// ===============================
// FILTROS TORNEOS
// ===============================
(function () {
  const yearEl = document.getElementById("filterYear");
  const monthEl = document.getElementById("filterMonth");
  const cityEl = document.getElementById("filterCity");

  const btnApply = document.getElementById("btnApplyFilters");
  const btnClear = document.getElementById("btnClearFilters");

  const grid = document.getElementById("tournamentsGrid");
  const emptyState = document.getElementById("emptyState");
  const hint = document.getElementById("gpResultsHint");

  if (!yearEl || !monthEl || !cityEl || !grid) return;

  function slugify(str) {
    return (str || "")
      .toString()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "") // saca acentos
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  function applyFilters() {
    const y = yearEl.value.trim();
    const m = monthEl.value.trim();
    const c = slugify(cityEl.value);

    const cards = grid.querySelectorAll(".gp-torneo-card");
    let visibleCount = 0;

    cards.forEach((card) => {
      const cy = card.getAttribute("data-year") || "";
      const cm = card.getAttribute("data-month") || "";
      const cc = card.getAttribute("data-city") || "";

      let show = true;

      if (y && cy !== y) show = false;
      if (m && cm !== m) show = false;
      if (c && !cc.includes(c)) show = false;

      card.style.display = show ? "" : "none";
      if (show) visibleCount++;
    });

    if (emptyState) {
      emptyState.classList.toggle("d-none", visibleCount !== 0);
    }

    if (hint) {
      hint.textContent = visibleCount
        ? `Mostrando ${visibleCount} torneo(s) según tu selección.`
        : "No hay resultados con esos filtros.";
    }
  }

  function clearFilters() {
    yearEl.value = "";
    monthEl.value = "";
    cityEl.value = "";
    applyFilters();
  }

  if (btnApply) btnApply.addEventListener("click", applyFilters);
  if (btnClear) btnClear.addEventListener("click", clearFilters);

  // Aplicar al cargar
  applyFilters();
})();
