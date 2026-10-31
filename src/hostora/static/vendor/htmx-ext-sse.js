(function () {
  "use strict";
  function connectSseElement(el) {
    if (el.__sseConnected) return;
    var url = el.getAttribute("sse-connect");
    if (!url) return;
    el.__sseConnected = true;
    var swapAttr = el.getAttribute("sse-swap") || "message";
    var events = swapAttr.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var es = new EventSource(url, { withCredentials: true });
    el.__eventSource = es;

    events.forEach(function (evtName) {
      es.addEventListener(evtName, function (ev) {
        var subTarget = el.querySelector('[sse-swap="' + evtName + '"]');
        if (subTarget) {
          var mode = subTarget.getAttribute("hx-swap") || "innerHTML";
          if (mode === "beforeend") {
            subTarget.insertAdjacentHTML("beforeend", ev.data);
          } else {
            subTarget.innerHTML = ev.data;
          }
        } else if (events.length === 1) {
          el.innerHTML = ev.data;
        } else {
          var stageBox = el.querySelector("[data-sse-target='" + evtName + "']");
          if (stageBox) {
            if (evtName === "category") {
              stageBox.insertAdjacentHTML("beforeend", ev.data);
            } else {
              stageBox.innerHTML = ev.data;
            }
          } else {
            el.insertAdjacentHTML("beforeend", ev.data);
          }
        }
        if (evtName === "done" || evtName === "error") {
          es.close();
        }
        document.dispatchEvent(new CustomEvent("htmx:afterSettle", { detail: { target: el } }));
      });
    });
  }

  function scanSse() {
    var nodes = document.querySelectorAll("[sse-connect]");
    for (var i = 0; i < nodes.length; i++) connectSseElement(nodes[i]);
  }

  document.addEventListener("DOMContentLoaded", scanSse);
  document.addEventListener("htmx:afterSettle", scanSse);
})();

