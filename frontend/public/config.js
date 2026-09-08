(function () {
  var local = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  window.__VOLTERRA_API_BASE__ = local ? 'http://localhost:8090' : 'https://volterra-api.onrender.com';
})();
