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
  var newsMarkers = [];
  var lastCandleTime = 0;

  var TZ = -new Date().getTimezoneOffset() * 60; // chart library renders UTC; shift to local time
  function local(c) { return { time: c.time + TZ, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume }; }
  function volBar(c) {
    return { time: c.time, value: c.volume, color: c.close >= c.open ? "rgba(34,211,126,.35)" : "rgba(255,77,94,.35)" };
  }
  function pushCandle(c) {
    c = local(c);
    if (c.time < lastCandleTime) return;
    lastCandleTime = c.time;
    candles.update(c);
    volume.update(volBar(c));
  }
  function loadHistory(d) {
    var cs = d.candles.map(local);
    candles.setData(cs);
    volume.setData(cs.map(volBar));
    lastCandleTime = cs.length ? cs[cs.length - 1].time : 0;
    newsMarkers = [];
    candles.setMarkers([]);
    $("feed").innerHTML = "";
    d.feed.forEach(addFeed);
    if (d.news.length) $("headline").textContent = d.news[d.news.length - 1].text;
    if (d.current) pushCandle(d.current);
  }

  // --- feed & news --------------------------------------------------------
  var LABEL = { buy: "BUY", sell: "SELL", hold: "HOLD" };
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

  function addFeed(f) {
    if (f.action === "hold" && Math.random() < 0.6 && !f.human) return; // keep the feed lively, not noisy
    var li = document.createElement("li");
    if (f.human) li.className = "human";
    var cls = f.action === "buy" ? "up" : f.action === "sell" ? "down" : "";
    var size = f.action === "hold" ? "" : " " + f.qty + (f.filled && f.filled < f.qty ? " (" + f.filled + " filled)" : "");
    li.innerHTML = '<span class="who">' + esc(f.emoji) + " @" + esc(f.handle) + '</span><span class="act ' + cls + '">' +
      LABEL[f.action] + size + "</span><br>" + esc(f.thought);
    var feed = $("feed");
    feed.insertBefore(li, feed.firstChild);
    while (feed.children.length > 14) feed.removeChild(feed.lastChild);
  }

  var GOOD = /(win|wins|surge|soar|record|cut|cuts|contract|buyback|beat|launch|approve|partner|breakthrough|moon|deal|rally)/i;
  var flashTimer = null;
  var SRC = { crowd: "leaked from a phone", voice: "leaked by voice", newsroom: "AI newsroom · Nemotron", real: "REAL headline · via Tavily", alexa: "leaked via Alexa" };
  function showNews(n, big) {
    $("headline").textContent = n.text;
    newsMarkers.push({ time: lastCandleTime || Math.floor(Date.now() / 1000) + TZ, position: "aboveBar", color: "#ffd23f", shape: "arrowDown", text: "📰" });
    newsMarkers = newsMarkers.slice(-12);
    try { candles.setMarkers(newsMarkers); } catch (e) {}
    if (!big) return;
    var el = $("flash");
    el.className = GOOD.test(n.text) ? "good" : "";
    $("flash-text").textContent = n.text;
    $("flash-src").textContent = (SRC[n.source] || n.source) + (n.author && n.author !== "anon" ? " · " + n.author : "");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { el.className = "hidden"; }, 4200);
  }

  // --- crash / moon alarm --------------------------------------------------
  var priceHist = [];
  var alarmUntil = 0;
  function checkAlarm(price) {
    var now = Date.now();
    priceHist.push([now, price]);
    while (priceHist.length && priceHist[0][0] < now - 12000) priceHist.shift();
    if (now < alarmUntil || priceHist.length < 5) return;
    var ref = priceHist[0][1], move = price / ref - 1;
    if (Math.abs(move) < 0.04) return;
    alarmUntil = now + 20000;
    var el = $("alarm");
    el.className = move < 0 ? "crash" : "moon";
    el.textContent = move < 0 ? "FLASH CRASH" : "TO THE MOON";
    setTimeout(function () { el.className = "hidden"; }, 2500);
  }

  // --- snapshot ------------------------------------------------------------
  var lastPrice = null;
  function fmt(n, d) { return Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function snapshot(s) {
    var p = $("price");
    p.textContent = fmt(s.price, 2);
    if (lastPrice !== null && s.price !== lastPrice) p.className = s.price > lastPrice ? "up" : "down";
    lastPrice = s.price;
    var ch = $("change");
    ch.textContent = (s.change_pct >= 0 ? "+" : "") + fmt(s.change_pct, 2) + "%";
    ch.className = "chg " + (s.change_pct >= 0 ? "up" : "down");
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
    $("btn-auto").textContent = "📰 Autopilot: " + (s.autopilot ? "on" : "off");
    $("btn-auto").classList.toggle("on", s.autopilot);
    if (s.current) pushCandle(s.current);
    checkAlarm(s.price);

    var rows = s.leaderboard.map(function (r) {
      var cls = r.pnl_pct >= 0 ? "up" : "down";
      return '<li class="' + (r.key === "human" ? "human" : "") + '"><span>' + esc(r.emoji) + " " + esc(r.label) +
        '</span><span class="pnl ' + cls + '">' + (r.pnl_pct >= 0 ? "+" : "") + fmt(r.pnl_pct, 1) + "%</span></li>";
    });
    s.players.slice(0, 4).forEach(function (pl) {
      rows.push('<li class="human"><span>🧑 ' + esc(pl.name) + " #" + pl.rank + '</span><span class="pnl">' +
        (pl.pnl_pct >= 0 ? "+" : "") + fmt(pl.pnl_pct, 1) + "%</span></li>");
    });
    $("board").innerHTML = rows.join("");

    var a = s.analytics || {};
    $("s-kurt").textContent = a.excess_kurtosis != null ? a.excess_kurtosis : "–";
    $("s-vc").textContent = a.vol_clustering != null ? a.vol_clustering : "–";
    $("s-dd").textContent = a.max_drawdown_pct != null ? a.max_drawdown_pct + "%" : "–";
    $("s-herd").textContent = a.herding != null ? Math.round(a.herding * 100) + "%" : "–";
    $("s-vol").textContent = fmt(s.volume, 0);
  }

  // --- websocket ------------------------------------------------------------
  function connect() {
    var ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onmessage = function (ev) {
      var m = JSON.parse(ev.data);
      if (m.type === "init" || m.type === "reset") { loadHistory(m.data); snapshot(m.data); }
      else if (m.type === "snapshot") snapshot(m.data);
      else if (m.type === "candle") pushCandle(m.candle);
      else if (m.type === "feed") addFeed(m.item);
      else if (m.type === "news") showNews(m.item, true);
    };
    ws.onclose = function () { setTimeout(connect, 1500); };
  }
  connect();

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
    if (news) return post("/api/news", { text: news, author: "TV remote" });
    if (act === "chaos") return post("/api/news/chaos");
    if (act === "real") return post("/api/news/real", {});
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
  document.addEventListener("keydown", function (e) {
    var k = e.key, c = e.keyCode;
    if (k === "ArrowRight" || k === "ArrowDown" || c === 22) { setFocus(focusIdx + 1); e.preventDefault(); }
    else if (k === "ArrowLeft" || k === "ArrowUp" || c === 21) { setFocus(focusIdx - 1); e.preventDefault(); }
    else if (k === "Enter" || c === 23 || c === 66) {
      if (document.activeElement !== buttons[focusIdx]) fire(buttons[focusIdx]);
    } else if (k === "MediaPlayPause" || c === 179 || c === 85) post("/api/control", { action: "toggle_pause" });
    else if (k === "MediaFastForward" || c === 228 || c === 90) post("/api/news/chaos");
    else if (k === "MediaRewind" || c === 227 || c === 89) post("/api/control", { action: "reset" });
  });
})();
