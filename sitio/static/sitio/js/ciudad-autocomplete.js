/**
 * Autocompletado de ciudades argentinas usando la API pública y gratuita
 * "Georef" del Gobierno de Argentina (sin necesidad de API key):
 * https://apis.datos.gob.ar/georef/api/localidades
 *
 * El dropdown de sugerencias se agrega a <body> con position:fixed,
 * calculando su posición con getBoundingClientRect() del input. Así
 * NUNCA empuja ni agranda el layout de la página, sea cual sea el
 * contenedor (grid, flex, etc.) donde viva el input.
 *
 * Uso:
 *   gpCiudadAutocomplete({
 *     inputId: 'gpCiudadInput',
 *     latInputId: 'gpLatInput',   // opcional
 *     lonInputId: 'gpLonInput',   // opcional
 *     onSelect: function (localidad) { ... }  // opcional
 *   });
 */
function gpCiudadAutocomplete(opts) {
  var input = document.getElementById(opts.inputId);
  if (!input) return;

  var box = document.createElement("div");
  box.className = "gp-ciudad-suggestions";
  box.style.display = "none";
  document.body.appendChild(box);

  var timer = null;
  var latInput = opts.latInputId ? document.getElementById(opts.latInputId) : null;
  var lonInput = opts.lonInputId ? document.getElementById(opts.lonInputId) : null;

  function posicionar() {
    var r = input.getBoundingClientRect();
    box.style.position = "fixed";
    box.style.top = r.bottom + "px";
    box.style.left = r.left + "px";
    box.style.width = r.width + "px";
  }

  function limpiar() {
    box.innerHTML = "";
    box.style.display = "none";
  }

  function buscar(q) {
    fetch(
      "https://apis.datos.gob.ar/georef/api/localidades?nombre=" +
        encodeURIComponent(q) +
        "&max=8&campos=estandar"
    )
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var localidades = data.localidades || [];
        box.innerHTML = "";
        if (!localidades.length) { limpiar(); return; }

        localidades.forEach(function (loc) {
          var item = document.createElement("div");
          item.className = "gp-ciudad-suggestion-item";
          item.textContent = loc.nombre + ", " + loc.provincia.nombre;
          item.addEventListener("mousedown", function (e) {
            // mousedown (no click) para que dispare ANTES del blur del input
            e.preventDefault();
            // Guardamos "Nombre, Provincia" (no solo el nombre): hay ciudades
            // homónimas en distintas provincias (ej. San Cayetano existe en
            // Buenos Aires Y en Corrientes) y esto evita ambigüedad.
            var etiqueta = loc.nombre + ", " + loc.provincia.nombre;
            input.value = etiqueta;
            if (latInput) latInput.value = Number(loc.centroide.lat).toFixed(7);
            if (lonInput) lonInput.value = Number(loc.centroide.lon).toFixed(7);
            limpiar();
            if (opts.onSelect) opts.onSelect(loc);
          });
          box.appendChild(item);
        });
        posicionar();
        box.style.display = "block";
      })
      .catch(limpiar);
  }

  input.addEventListener("input", function () {
    clearTimeout(timer);
    // Si el usuario vuelve a tipear después de haber elegido una ciudad,
    // invalidamos las coordenadas guardadas hasta que elija de nuevo.
    if (latInput) latInput.value = "";
    if (lonInput) lonInput.value = "";

    var q = input.value.trim();
    if (q.length < 3) { limpiar(); return; }
    timer = setTimeout(function () { buscar(q); }, 300);
  });

  input.addEventListener("blur", function () {
    // Pequeño delay para permitir que el mousedown de una sugerencia
    // se procese antes de cerrar el dropdown.
    setTimeout(limpiar, 150);
  });

  window.addEventListener("resize", posicionar);
  window.addEventListener("scroll", posicionar, true);

  document.addEventListener("click", function (e) {
    if (e.target !== input && !box.contains(e.target)) limpiar();
  });
}
