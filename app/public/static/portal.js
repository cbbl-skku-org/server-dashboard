// CBBL portal: small enhancements on top of server-rendered pages (the pages also work without this file).
(function () {
  "use strict";
  var root = document.documentElement;

  function expired(resp) {
    // Apache sends the browser to /login when the session has ended
    return resp.redirected && /\/login(\?|$)/.test(resp.url.replace(/^[a-z]+:\/\/[^/]+/, ""));
  }

  function gb(bytes) {
    var g = bytes / Math.pow(1024, 3);
    return g >= 10 ? g.toFixed(0) + " GB" : g >= 1 ? g.toFixed(1) + " GB" : (bytes / Math.pow(1024, 2)).toFixed(0) + " MB";
  }

  // light / dark
  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-theme-toggle]")) return;
    var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("cbbl-theme", next); } catch (err) { /* not remembered */ }
  });

  // whole table rows open the job
  document.addEventListener("click", function (e) {
    var tr = e.target.closest("tr[data-href]");
    if (tr && !e.target.closest("a, button, input, select, textarea") && !window.getSelection().toString()) {
      window.location.href = tr.getAttribute("data-href");
    }
  });

  // "Cancel job?" and similar: on the form, or on the button that sent it
  document.addEventListener("submit", function (e) {
    var msg = (e.submitter && e.submitter.getAttribute("data-confirm")) || e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  document.querySelectorAll("select[data-autosubmit]").forEach(function (s) {
    s.addEventListener("change", function () { s.form.submit(); });
  });

  // Copy buttons
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy]");
    if (!b) return;
    var src = document.querySelector(b.getAttribute("data-copy"));
    var text = src ? src.textContent : "";
    var label = b.lastChild;
    function done() {
      b.classList.add("copied");
      label.textContent = "Copied";
      setTimeout(function () { b.classList.remove("copied"); label.textContent = "Copy"; }, 1500);
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { if (document.execCommand("copy")) done(); } catch (err) { /* nothing to do */ }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  });

  // Regions that reload themselves: data-refresh="<url>" data-interval="<ms>"
  document.querySelectorAll("[data-refresh]").forEach(function (el) {
    var every = parseInt(el.getAttribute("data-interval"), 10) || 10000;
    var follow = el.getAttribute("data-scroll") === "end";
    if (follow) el.scrollTop = el.scrollHeight;
    function tick() {
      if (document.hidden) { setTimeout(tick, every); return; }
      fetch(el.getAttribute("data-refresh"), { headers: { "X-Partial": "1" }, credentials: "same-origin", cache: "no-store" })
        .then(function (r) {
          if (expired(r)) { window.location.reload(); return null; }
          return r.ok ? r.text() : null;
        })
        .then(function (html) {
          if (html === null || html === undefined) return;
          var atEnd = follow && el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
          if (el.innerHTML !== html) el.innerHTML = html;
          if (atEnd) el.scrollTop = el.scrollHeight;
        })
        .catch(function () { /* try again next time */ })
        .then(function () { setTimeout(tick, every); });
    }
    setTimeout(tick, every);
  });

  // Job page: reload when the job's state changes (waiting -> running -> finished). The query string is dropped so
  // that "Job submitted." does not stay on the page.
  var watch = document.querySelector("[data-job-watch]");
  if (watch) {
    var stateUrl = watch.getAttribute("data-job-watch");
    var seen = watch.getAttribute("data-state");
    var every = seen === "PENDING" ? 5000 : 10000;
    var checkState = function () {
      if (document.hidden) { setTimeout(checkState, every); return; }
      fetch(stateUrl, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
        if (expired(r)) { window.location.reload(); return null; }
        return r.ok ? r.json() : null;
      }).then(function (d) {
        if (d && d.state && d.state !== seen) { window.location.replace(window.location.pathname); return; }
        setTimeout(checkState, every);
      }).catch(function () { setTimeout(checkState, every * 2); });
    };
    setTimeout(checkState, every);
  }

  // Timeline: start with "now" in view
  var tl = document.querySelector(".timeline[data-now-x]");
  if (tl) tl.scrollLeft = Math.max(0, parseInt(tl.getAttribute("data-now-x"), 10) - tl.clientWidth * 0.55);

  // New job: live equivalent command, field checks and script preview
  var form = document.querySelector("form[data-preview-url]");
  if (form) {
    var cmd = document.getElementById("cmd-text");
    var scriptCard = document.getElementById("script-card");
    var scriptText = document.getElementById("script-text");
    var touched = {};
    var timer = null;
    // A field showing a default (RAM from the CPUs, job name from the command) keeps following it until the user
    // types a value of their own: it is "following" while it still equals the value the page last filled in.
    // An emptied field gets the default back once the user leaves it (not while they are typing a new value).
    var follow = function (input, last, value) {
      if (!input || !last || value === undefined) return;
      var now = input.value.trim();
      if (now === last.value.trim() || (now === "" && document.activeElement !== input)) input.value = value;
      last.value = value;
    };
    var update = function (showAll) {
      fetch(form.getAttribute("data-preview-url"), {
        method: "POST", body: new URLSearchParams(new FormData(form)), credentials: "same-origin", cache: "no-store"
      }).then(function (r) {
        if (expired(r)) { window.location.reload(); return null; }
        return r.ok ? r.json() : null;
      }).then(function (d) {
        if (!d) return;
        cmd.textContent = d.command;
        scriptText.textContent = d.script;
        follow(form.elements.namedItem("mem"), form.elements.namedItem("mem_default"), d.mem_default);
        follow(form.elements.namedItem("name"), form.elements.namedItem("name_default"), d.name_default);
        form.querySelectorAll("[data-error-for]").forEach(function (el) {
          var name = el.getAttribute("data-error-for");
          var msg = (showAll || touched[name]) && d.errors[name] ? d.errors[name] : "";
          el.textContent = msg;
          el.parentElement.classList.toggle("has-error", !!msg);
        });
        if (showAll) {
          scriptCard.hidden = false;
          scriptCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }).catch(function () { /* the server check on Submit still applies */ });
    };
    var later = function (e) {
      if (e.target.name) touched[e.target.name] = true;
      clearTimeout(timer);
      timer = setTimeout(function () { update(false); }, 250);
    };
    form.addEventListener("input", later);
    form.addEventListener("change", later);
    var quickBox = form.querySelector("input[name=quick]");
    if (quickBox) {  // a quick test may run at most the test maximum: switch the time limit, and back
      var timeInput = form.elements.namedItem("time");
      var normalTime = timeInput.value;
      quickBox.addEventListener("change", function () {
        if (quickBox.checked) { normalTime = timeInput.value; timeInput.value = quickBox.getAttribute("data-quick-time"); }
        else { timeInput.value = normalTime; }
      });
    }
    var pv = form.querySelector("[data-preview]");
    if (pv) pv.addEventListener("click", function (e) { e.preventDefault(); update(true); });
    update(false);
  }

  // Running job: CPU and memory, sampled while the page is open
  var live = document.querySelector("[data-usage]");
  if (live) {
    var url = live.getAttribute("data-usage");
    var cpus = parseFloat(live.getAttribute("data-cpus")) || 1;
    var memMb = parseFloat(live.getAttribute("data-mem-mb")) || 0;
    var cpuText = live.querySelector("[data-cpu-text]");
    var memText = live.querySelector("[data-mem-text]");
    var cpuFill = live.querySelector(".cpu-meter .fill");
    var memFill = live.querySelector(".mem-meter .fill");
    var lineCpu = live.querySelector(".spark-cpu");
    var lineMem = live.querySelector(".spark-mem");
    var samples = [];
    var prev = null;
    var draw = function () {
      var n = samples.length;
      var pts = function (key) {
        var list = n === 1 ? [samples[0], samples[0]] : samples;
        return list.map(function (s, i) {
          var x = (i / (list.length - 1)) * 600;
          return x.toFixed(1) + "," + (118 - Math.min(1, s[key]) * 112).toFixed(1);
        }).join(" ");
      };
      lineCpu.setAttribute("points", pts("cpu"));
      lineMem.setAttribute("points", pts("mem"));
    };
    var poll = function () {
      fetch(url, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
        if (expired(r)) { window.location.reload(); return null; }
        return r.ok ? r.json() : null;
      }).then(function (d) {
        if (!d) return;
        if (!d.running) {  // the state watcher above reloads the page
          cpuText.textContent = "the job is no longer running";
          return;
        }
        if (d.cpu_seconds === undefined) {
          cpuText.textContent = d.error ? "not available: " + d.error : "first measurement after about 30 s";
          memText.textContent = "";
          setTimeout(poll, 15000);
          return;
        }
        var avg = d.elapsed ? d.cpu_seconds / d.elapsed : 0;
        var rate = avg;
        if (prev && d.cpu_seconds > prev.cpu_seconds && d.t > prev.t) rate = (d.cpu_seconds - prev.cpu_seconds) / (d.t - prev.t);
        if (!prev || d.cpu_seconds !== prev.cpu_seconds) prev = { t: d.t, cpu_seconds: d.cpu_seconds };
        cpuText.textContent = avg.toFixed(1) + " of " + cpus + " CPUs busy on average (" + Math.round(avg / cpus * 100) + "%)";
        cpuFill.style.width = Math.min(100, avg / cpus * 100) + "%";
        var memFrac = memMb ? d.mem_bytes / (memMb * 1048576) : 0;
        memText.textContent = gb(d.mem_bytes) + " now · peak " + gb(d.peak_bytes) + (memMb ? " of " + gb(memMb * 1048576) : "");
        memFill.style.width = Math.min(100, memFrac * 100) + "%";
        samples.push({ cpu: rate / cpus, mem: memFrac });
        if (samples.length > 240) samples.shift();
        draw();
        setTimeout(poll, 15000);
      }).catch(function () { setTimeout(poll, 30000); });
    };
    poll();
  }
})();
