(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };

  // --- fit the 1920x1080 stage to any TV / WebView size ------------------
  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    $("app").style.transform = "scale(" + s + ")";
  }
  window.addEventListener("resize", fit);
  fit();

  // --- sound: synthesized with WebAudio, no files ----------------------------
  var Snd = (function () {
    var ctx = null, on = localStorage.getItem("fc_sound") !== "0";
    function ac() {
      if (!ctx) { try { ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; } }
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    }
    function tone(freq, at, dur, type, vol, to) {
      var c = on && ac(); if (!c) return;
      var t = c.currentTime + at, o = c.createOscillator(), g = c.createGain();
      o.type = type || "sine";
      o.frequency.setValueAtTime(freq, t);
      if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(vol || 0.15, t + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(g); g.connect(c.destination);
      o.start(t); o.stop(t + dur + 0.05);
    }
    return {
      news: function () { tone(880, 0, .12, "square", .06); tone(660, .14, .12, "square", .06); tone(990, .28, .3, "square", .07); },
      crash: function () { tone(900, 0, 1.3, "sawtooth", .1, 110); tone(450, .1, 1.2, "sawtooth", .06, 60); },
      moon: function () { [523, 659, 784, 1047, 1319].forEach(function (f, i) { tone(f, i * .08, .3, "triangle", .14); }); },
      cash: function () { tone(1568, 0, .07, "square", .05); tone(2093, .06, .2, "square", .05); },
      sec: function () { for (var i = 0; i < 4; i++) { tone(650, i * .5, .25, "sawtooth", .08, 1050); tone(1050, i * .5 + .25, .25, "sawtooth", .08, 650); } },
      halt: function () { tone(140, 0, .4, "square", .14); tone(140, .5, .4, "square", .14); tone(105, 1, .7, "square", .14); },
      fanfare: function () { [[523, 0], [659, .15], [784, .3], [1047, .45], [784, .75], [1047, .9]].forEach(function (n) { tone(n[0], n[1], .35, "triangle", .16); }); },
      toggle: function () { on = !on; localStorage.setItem("fc_sound", on ? "1" : "0"); if (on) ac(); return on; },
      wake: ac,
      on: function () { return on; },
    };
  })();
  function sndLabel() { $("snd").textContent = Snd.on() ? "🔊" : "🔇"; }

  // --- stocks ---------------------------------------------------------------
  // The chart follows one stock at a time: ▲ ▼ on the remote switch, breaking news jumps to the stock it's about.
  var markets = [];          // latest summary from the server
  var store = {};            // ticker -> {candles: [...], markers: [...]}
  var focus = "GPU";
  var manualUntil = 0;       // after a manual switch, don't auto-follow news for a while
  function S(t) { return store[t] || (store[t] = { candles: [], markers: [] }); }
  function company(t) { for (var i = 0; i < markets.length; i++) if (markets[i].ticker === t) return markets[i].company; return t; }

  // --- chart --------------------------------------------------------------
  var chartEl = $("chart");
  var chart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth, height: chartEl.clientHeight,
    layout: { background: { color: "#0f131a" }, textColor: "#7d8899", fontSize: 16 },
    grid: { vertLines: { color: "#161c26" }, horzLines: { color: "#161c26" } },
    timeScale: { timeVisible: true, secondsVisible: true, borderColor: "#1d2430", rightOffset: 4, barSpacing: 12 },
    rightPriceScale: { borderColor: "#1d2430" },
    crosshair: { mode: 0 },
  });
  var candles = chart.addCandlestickSeries({
    upColor: "#22d37e", downColor: "#ff4d5e", wickUpColor: "#22d37e", wickDownColor: "#ff4d5e", borderVisible: false,
  });
  var volume = chart.addHistogramSeries({ priceScaleId: "", priceFormat: { type: "volume" } });
  volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

  var TZ = -new Date().getTimezoneOffset() * 60; // chart library renders UTC; shift to local time
  function local(c) { return { time: c.time + TZ, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume }; }
  function volBar(c) {
    return { time: c.time, value: c.volume, color: c.close >= c.open ? "rgba(34,211,126,.35)" : "rgba(255,77,94,.35)" };
  }
  function lastTime(t) { var cs = S(t).candles; return cs.length ? cs[cs.length - 1].time : 0; }
  function pushCandle(t, c) {
    c = local(c);
    var cs = S(t).candles;
    if (cs.length && c.time < cs[cs.length - 1].time) return;
    if (cs.length && cs[cs.length - 1].time === c.time) cs[cs.length - 1] = c; else cs.push(c);
    if (cs.length > 900) cs.shift();
    if (t === focus) { candles.update(c); volume.update(volBar(c)); }
  }
  function drawFocus() {
    var st = S(focus);
    candles.setData(st.candles);
    volume.setData(st.candles.map(volBar));
    try { candles.setMarkers(st.markers); } catch (e) {}
    chart.timeScale().scrollToRealTime();
    $("sym").textContent = "$" + focus;
    renderTickers();
    renderStats();
  }
  function setFocusStock(t, manual) {
    if (!t || !store[t]) return;
    if (manual) manualUntil = Date.now() + 20000;
    if (t === focus) return;
    focus = t;
    lastPrice = null;
    drawFocus();
  }
  function stepStock(d) {
    var ts = markets.map(function (m) { return m.ticker; });
    var i = ts.indexOf(focus);
    setFocusStock(ts[(i + d + ts.length) % ts.length], true);
  }

  function loadHistory(d) {
    store = {};
    Object.keys(d.candles).forEach(function (t) { S(t).candles = d.candles[t].map(local); });
    Object.keys(d.current || {}).forEach(function (t) { pushCandle(t, d.current[t]); });
    $("feed").innerHTML = "";
    queue = [];
    d.feed.filter(function (f) { return f.action !== "hold"; }).slice(-8).forEach(addFeed);
    if (d.news.length) $("headline").textContent = d.news[d.news.length - 1].text;
    drawFocus();
  }

  // --- feed & news --------------------------------------------------------
  var LABEL = { buy: "BUY", sell: "SELL", hold: "HOLD" };
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

  // Hundreds of agents post every second; the TV shows a readable trickle instead.
  // Posts queue up and one is shown every FEED_MS, human players, the stock on screen and big trades first.
  var FEED_MS = 1600, QUEUE_MAX = 40;
  var queue = [];
  function interest(f) {
    if (f.human) return 1e9;
    if (f.action === "hold") return 0;
    return Math.sqrt(f.filled || f.qty * 0.2) + Math.random() * 6 + (f.ticker === focus ? 4 : 0);
  }
  function queueFeed(f) {
    if (f.human && f.filled) Snd.cash();
    if (!f.thought && !f.human) return;
    if (f.action !== "hold" && !f.qty) return;
    queue.push(f);
    if (queue.length > QUEUE_MAX) {
      queue.sort(function (a, b) { return interest(b) - interest(a) || b.ts - a.ts; });
      queue.length = QUEUE_MAX;
    }
  }
  setInterval(function () {
    if (!queue.length) return;
    var best = 0;
    for (var i = 1; i < queue.length; i++) if (interest(queue[i]) > interest(queue[best])) best = i;
    addFeed(queue.splice(best, 1)[0]);
    queue = queue.filter(function (f) { return f.human || Date.now() / 1000 - f.ts < 12; }); // stale posts are dropped
  }, FEED_MS);

  function addFeed(f) {
    var li = document.createElement("li");
    if (f.human) li.className = "human";
    var cls = f.action === "buy" ? "up" : f.action === "sell" ? "down" : "";
    var size = f.action === "hold" ? "" : " " + f.qty + (f.filled && f.filled < f.qty ? " (" + f.filled + " filled)" : "");
    li.innerHTML = '<span class="who">' + esc(f.emoji) + " @" + esc(f.handle) + '</span><span class="act ' + cls + '">' +
      LABEL[f.action] + size + '</span><span class="tkr">$' + esc(f.ticker) + "</span><br>" + esc(f.thought);
    var feed = $("feed");
    feed.insertBefore(li, feed.firstChild);
    while (feed.children.length > 14) feed.removeChild(feed.lastChild);
  }

  var GOOD = /(win|wins|surge|soar|record|cut|cuts|contract|buyback|beat|launch|approve|partner|breakthrough|moon|deal|rally|golden|gain|celebrate|smash|raise|accept)/i;
  var flashTimer = null;
  var SRC = { crowd: "leaked from a phone", voice: "leaked by voice", newsroom: "AI newsroom", real: "REAL headline · via Tavily", alexa: "leaked via Alexa" };
  function showNews(n, big) {
    $("headline").textContent = n.text;
    (n.tickers || [focus]).forEach(function (t) {
      var st = S(t);
      st.markers.push({ time: lastTime(t) || Math.floor(Date.now() / 1000) + TZ, position: "aboveBar", color: "#ffd23f", shape: "arrowDown", text: "📰" });
      st.markers = st.markers.slice(-12);
      hot[t] = Date.now();
    });
    if (n.tickers && n.tickers.length === 1 && Date.now() > manualUntil) setFocusStock(n.tickers[0], false);
    try { candles.setMarkers(S(focus).markers); } catch (e) {}
    renderTickers();
    if (!big) return;
    var el = $("flash");
    el.className = GOOD.test(n.text) ? "good" : "";
    $("flash-text").textContent = n.text;
    var who = n.tickers && n.tickers.length < markets.length ? n.tickers.map(function (t) { return "$" + t; }).join(" · ") + " · " : "WHOLE MARKET · ";
    $("flash-src").textContent = who + (SRC[n.source] || n.source) + (n.author && n.author !== "anon" ? " · " + n.author : "") +
      (n.cred != null && n.cred < 0.8 ? " · 🤨 credibility " + Math.round(n.cred * 100) + "%" : "");
    Snd.news();
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { el.className = "hidden"; }, 4200);
  }

  // --- crash / moon alarm (any stock) ----------------------------------------
  var priceHist = {};
  var alarmUntil = 0;
  function checkAlarm(t, price) {
    var now = Date.now();
    var h = priceHist[t] || (priceHist[t] = []);
    h.push([now, price]);
    while (h.length && h[0][0] < now - 12000) h.shift();
    if (now < alarmUntil || h.length < 5) return;
    var move = price / h[0][1] - 1;
    if (Math.abs(move) < 0.04) return;
    alarmUntil = now + 20000;
    if (Date.now() > manualUntil) setFocusStock(t, false);
    var el = $("alarm");
    el.className = move < 0 ? "crash" : "moon";
    el.innerHTML = '<div style="text-align:center">$' + esc(t) + "<br>" + (move < 0 ? "FLASH CRASH" : "TO THE MOON") + "</div>";
    if (move < 0) Snd.crash(); else Snd.moon();
    setTimeout(function () { el.className = "hidden"; }, 2500);
  }

  // --- snapshot ------------------------------------------------------------
  var lastPrice = null, hot = {}, analytics = {};
  function fmt(n, d) { return Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function sign(n, d) { return (n >= 0 ? "+" : "") + fmt(n, d); }
  function renderTickers() {
    $("tickers").innerHTML = markets.map(function (m) {
      var cls = "tk" + (m.ticker === focus ? " on" : "") + (Date.now() - (hot[m.ticker] || 0) < 3000 ? " hot" : "") + (m.halted ? " halted" : "");
      return '<div class="' + cls + '"><b>$' + esc(m.ticker) + '</b><span class="px">' + fmt(m.price, 2) + '</span>' +
        '<span class="co">' + esc(m.company) + '</span><span class="ch ' + (m.change_pct >= 0 ? "up" : "down") + '">' +
        sign(m.change_pct, 2) + '%</span><span class="bar" title="AI buying vs selling, last 30s"><i style="width:' +
        Math.round((m.buy_pressure == null ? 0.5 : m.buy_pressure) * 100) + '%"></i></span></div>';
    }).join("");
  }
  function renderStats() {
    var a = analytics[focus] || {};
    $("s-kurt").textContent = a.excess_kurtosis != null ? a.excess_kurtosis : "–";
    $("s-vc").textContent = a.vol_clustering != null ? a.vol_clustering : "–";
    $("s-dd").textContent = a.max_drawdown_pct != null ? a.max_drawdown_pct + "%" : "–";
    $("s-herd").textContent = a.herding != null ? Math.round(a.herding * 100) + "%" : "–";
    var m = markets.filter(function (x) { return x.ticker === focus; })[0];
    $("s-vol").textContent = m ? fmt(m.volume, 0) : "0";
  }
  function snapshot(s) {
    markets = s.markets;
    analytics = s.analytics || {};
    var m = markets.filter(function (x) { return x.ticker === focus; })[0] || markets[0];
    var p = $("price");
    p.textContent = fmt(m.price, 2);
    if (lastPrice !== null && m.price !== lastPrice) p.className = m.price > lastPrice ? "up" : "down";
    lastPrice = m.price;
    var ch = $("change");
    ch.textContent = sign(m.change_pct, 2) + "%";
    ch.className = "chg " + (m.change_pct >= 0 ? "up" : "down");
    $("agents").textContent = s.agents;
    $("cpm").textContent = fmt(s.brain.calls_per_min, 0);
    $("tps").textContent = fmt(s.brain.tokens_per_s, 0);
    var mode = $("mode");
    if (s.brain.mode === "llm") {
      mode.textContent = s.brain.provider === "nebius" ? "NVIDIA NEMOTRON · NEBIUS" : s.brain.provider === "bedrock" ? "AMAZON BEDROCK" : "LIVE LLM";
      mode.className = "badge live";
    } else { mode.textContent = "OFFLINE MOCK"; mode.className = "badge"; }
    $("model").textContent = s.brain.fast_model + (s.brain.big_model !== s.brain.fast_model ? " · whales: " + s.brain.big_model : "");
    $("btn-pause").textContent = s.paused ? "▶ Resume" : "⏸ Pause";
    $("btn-auto").textContent = "📰 Auto: " + (s.autopilot ? "on" : "off");
    $("btn-auto").classList.toggle("on", s.autopilot);
    Object.keys(s.current || {}).forEach(function (t) { pushCandle(t, s.current[t]); });
    markets.forEach(function (x) { checkAlarm(x.ticker, x.price); });

    var r = $("round");
    if (s.round_left != null) {
      r.classList.remove("hidden");
      r.classList.toggle("low", s.round_left <= 30);
      $("round-left").textContent = Math.floor(s.round_left / 60) + ":" + ("0" + (s.round_left % 60)).slice(-2);
    } else r.classList.add("hidden");
    if (!s.round_over) $("podium").classList.add("hidden");

    var rows = s.leaderboard.map(function (r) {
      var cls = r.pnl_pct >= 0 ? "up" : "down";
      return '<li class="' + (r.key === "human" ? "human" : "") + '"><span>' + esc(r.emoji) + " " + esc(r.label) +
        '</span><span class="pnl ' + cls + '">' + sign(r.pnl_pct, 1) + "%</span></li>";
    });
    s.players.slice(0, 4).forEach(function (pl) {
      rows.push('<li class="human"><span>🧑 ' + esc(pl.name) + " #" + pl.rank + '</span><span class="pnl">' + sign(pl.pnl_pct, 1) + "%</span></li>");
    });
    $("board").innerHTML = rows.join("");
    renderTickers();
    renderStats();
  }

  // --- forensics: why did it move? --------------------------------------------
  var whyTimer = null;
  function showWhy(d) {
    if (Date.now() > manualUntil) setFocusStock(d.ticker, false);
    $("why-move").textContent = "$" + d.ticker + " " + sign(d.move_pct, 1) + "%";
    $("why-move").className = d.move_pct >= 0 ? "up" : "down";
    $("why-text").textContent = d.summary;
    var max = Math.max.apply(null, d.drivers.map(function (x) { return Math.abs(x.net_usd); }).concat([1]));
    $("why-bars").innerHTML = d.drivers.slice(0, 4).map(function (x) {
      var w = Math.max(4, Math.round(Math.abs(x.net_usd) / max * 300));
      return '<div><span class="nm">' + esc(x.emoji) + " " + esc(x.label) + '</span><span class="b" style="width:' + w + "px;background:" +
        (x.net_usd >= 0 ? "var(--up)" : "var(--down)") + '"></span><span class="v">' + (x.net_usd >= 0 ? "bought " : "sold ") + "$" +
        fmt(Math.abs(x.net_usd) / 1000, 0) + "k</span></div>";
    }).join("");
    $("why").classList.remove("hidden");
    clearTimeout(whyTimer);
    whyTimer = setTimeout(function () { $("why").classList.add("hidden"); }, 12000);
  }

  // --- the AI SEC -----------------------------------------------------------------
  var secTimer = null;
  function showSec(c) {
    $("sec-title").textContent = c.player + " fined $" + fmt(c.fine, 0);
    $("sec-text").textContent = c.text;
    $("sec").classList.remove("hidden");
    Snd.sec();
    clearTimeout(secTimer);
    secTimer = setTimeout(function () { $("sec").classList.add("hidden"); }, 7000);
    var li = document.createElement("li");
    li.className = "sec";
    li.innerHTML = '<span class="who">👮 @AI_SEC</span><span class="act down">FINE $' + fmt(c.fine, 0) + '</span><span class="tkr">$' +
      esc(c.ticker) + "</span><br>" + esc(c.text);
    $("feed").insertBefore(li, $("feed").firstChild);
  }

  // --- circuit breaker ---------------------------------------------------------------
  var haltTimer = null;
  function showHalt(h) {
    if (Date.now() > manualUntil) setFocusStock(h.ticker, false);
    $("halt-title").textContent = "$" + h.ticker + " HALTED";
    var left = h.seconds;
    function tick() {
      $("halt-sub").textContent = "moved " + sign(h.move_pct, 1) + "% in 20s · trading resumes in " + left + "s";
      if (left-- <= 0) { $("halt").classList.add("hidden"); return; }
      haltTimer = setTimeout(tick, 1000);
    }
    clearTimeout(haltTimer);
    $("why").classList.add("hidden");
    $("halt").classList.remove("hidden");
    tick();
    Snd.halt();
  }

  function roundOver(d) {
    var medals = ["🥇", "🥈", "🥉"];
    var list = d.players.slice(0, 5).map(function (p, i) {
      return "<li><span>" + (medals[i] || "#" + (i + 1)) + " " + esc(p.name) + '</span><span class="' + (p.pnl_pct >= 0 ? "up" : "down") + '">' +
        sign(p.pnl_pct, 1) + "%</span></li>";
    });
    if (!list.length) list.push("<li><span>No human players this round</span></li>");
    $("pod-list").innerHTML = list.join("");
    var ai = d.best_ai;
    var humanBest = d.players.length ? d.players[0].pnl_pct : null;
    $("pod-ai").textContent = ai ? "Best AI: " + ai.emoji + " " + ai.label + " " + sign(ai.pnl_pct, 1) + "%" +
      (humanBest != null ? (humanBest > ai.pnl_pct ? " · Humans beat the bots! 🎉" : " · The bots win this time 🤖") : "") : "";
    $("pod-recap").textContent = d.recap ? "🎙️ " + d.recap : "";
    $("podium").classList.remove("hidden");
    Snd.fanfare();
  }

  // --- websocket ------------------------------------------------------------
  function connect() {
    var ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onmessage = function (ev) {
      var m = JSON.parse(ev.data);
      if (m.type === "init" || m.type === "reset") { snapshot(m.data); loadHistory(m.data); }
      else if (m.type === "snapshot") snapshot(m.data);
      else if (m.type === "candle") pushCandle(m.ticker, m.candle);
      else if (m.type === "feed") queueFeed(m.item);
      else if (m.type === "news") showNews(m.item, true);
      else if (m.type === "round_over") roundOver(m.data);
      else if (m.type === "explain") showWhy(m.item);
      else if (m.type === "sec") showSec(m.item);
      else if (m.type === "halt") showHalt(m);
    };
    ws.onclose = function () { setTimeout(connect, 1500); };
  }
  connect();
  sndLabel();
  document.addEventListener("click", function () { Snd.wake(); });

  // --- join QR ----------------------------------------------------------------
  fetch("/api/info").then(function (r) { return r.json(); }).then(function (info) {
    $("join-url").textContent = info.join_url.replace(/^https?:\/\//, "");
    var qr = qrcode(0, "M");
    qr.addData(info.join_url);
    qr.make();
    $("qr").innerHTML = qr.createImgTag(5, 0);
    if (!info.tavily) $("btn-real").style.display = "none";
    initFocus();
  });

  // --- controls & D-pad remote ----------------------------------------------
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  }
  function fire(btn) {
    var act = btn.getAttribute("data-act"), news = btn.getAttribute("data-news");
    if (news) return post("/api/news", { text: news.replace("{co}", company(focus)), author: "TV remote", ticker: focus });
    if (act === "chaos") return post("/api/news/chaos", { ticker: focus });
    if (act === "real") return post("/api/news/real", {});
    if (act === "start_round") return post("/api/control", { action: act, minutes: 5 });
    return post("/api/control", { action: act });
  }
  var buttons = [];
  var focusIdx = 0;
  function initFocus() {
    buttons = Array.prototype.filter.call(document.querySelectorAll("#controls .btn"), function (b) { return b.style.display !== "none"; });
    buttons.forEach(function (b, i) {
      b.addEventListener("click", function () { focusIdx = i; fire(b); });
    });
    setFocus(0);
  }
  function setFocus(i) {
    focusIdx = (i + buttons.length) % buttons.length;
    buttons[focusIdx].focus();
  }
  $("tickers").addEventListener("click", function (e) {
    var tile = e.target.closest(".tk");
    if (tile) setFocusStock(tile.querySelector("b").textContent.slice(1), true);
  });
  document.addEventListener("keydown", function (e) {
    var k = e.key, c = e.keyCode;
    Snd.wake(); // browsers only allow audio after the first key press
    if (k === "m" || k === "M") { Snd.toggle(); sndLabel(); return; }
    if (k === "ArrowRight" || c === 22) { setFocus(focusIdx + 1); e.preventDefault(); }
    else if (k === "ArrowLeft" || c === 21) { setFocus(focusIdx - 1); e.preventDefault(); }
    else if (k === "ArrowUp" || c === 19) { stepStock(-1); e.preventDefault(); }
    else if (k === "ArrowDown" || c === 20) { stepStock(1); e.preventDefault(); }
    else if (k === "Enter" || c === 23 || c === 66) {
      if (document.activeElement !== buttons[focusIdx]) fire(buttons[focusIdx]);
    } else if (k === "MediaPlayPause" || c === 179 || c === 85) post("/api/control", { action: "toggle_pause" });
    else if (k === "MediaFastForward" || c === 228 || c === 90) post("/api/news/chaos", { ticker: focus });
    else if (k === "MediaRewind" || c === 227 || c === 89) post("/api/control", { action: "start_round", minutes: 5 });
    else if (k === "d" || k === "D") post("/api/control", { action: "demo" }); // scripted 3-minute show for recordings
  });
})();
