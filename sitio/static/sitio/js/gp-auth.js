/* Modal de login reutilizable: gpAuth.requireAuth(callback) ejecuta el
 * callback directo si ya hay sesión, o abre el modal, loguea por fetch
 * (sin recargar la página) y recién ahí ejecuta el callback — así el
 * usuario vuelve exactamente a la acción que estaba haciendo. */
window.gpAuth = (function () {
  var pendingCallback = null;

  function overlay() { return document.getElementById('gpAuthModal'); }

  function open(onSuccess) {
    pendingCallback = onSuccess || null;
    var errBox = document.getElementById('gpAuthError');
    if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
    var form = document.getElementById('gpAuthForm');
    if (form) form.reset();

    var googleLink = overlay().querySelector('.gp-auth-google');
    if (googleLink) {
      var base = googleLink.getAttribute('data-base-href') || googleLink.href;
      googleLink.setAttribute('data-base-href', base);
      var sep = base.indexOf('?') === -1 ? '?' : '&';
      googleLink.href = base + sep + 'next=' + encodeURIComponent(window.location.pathname + window.location.search);
    }

    overlay().classList.add('is-open');
    document.body.style.overflow = 'hidden';
    setTimeout(function () {
      var u = document.getElementById('gpAuthUser');
      if (u) u.focus();
    }, 50);
  }

  function close() {
    overlay().classList.remove('is-open');
    document.body.style.overflow = '';
    pendingCallback = null;
  }

  function requireAuth(isLoggedIn, onSuccess) {
    if (isLoggedIn) {
      onSuccess();
      return;
    }
    open(onSuccess);
  }

  function csrfToken() {
    var input = document.querySelector('#gpAuthForm input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function initForm() {
    var form = document.getElementById('gpAuthForm');
    if (!form) return;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var submitBtn = document.getElementById('gpAuthSubmit');
      var errBox = document.getElementById('gpAuthError');
      submitBtn.disabled = true;
      submitBtn.textContent = 'Ingresando…';

      var data = new FormData(form);
      fetch(form.action || window.gpLoginUrl, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        body: data,
      })
        .then(function (r) { return r.json().then(function (json) { return { status: r.status, json: json }; }); })
        .then(function (res) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Iniciar sesión';
          if (res.json.ok) {
            var cb = pendingCallback;
            close();
            if (cb) cb();
          } else {
            errBox.textContent = res.json.error || 'Usuario o contraseña incorrectos.';
            errBox.style.display = 'block';
          }
        })
        .catch(function () {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Iniciar sesión';
          errBox.textContent = 'No pudimos conectar. Probá de nuevo.';
          errBox.style.display = 'block';
        });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initForm();
    var ov = overlay();
    if (ov) {
      ov.addEventListener('click', function (e) {
        if (e.target === ov) close();
      });
    }
  });

  return { open: open, close: close, requireAuth: requireAuth };
})();
