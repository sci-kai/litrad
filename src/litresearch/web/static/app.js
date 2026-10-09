// litresearch UI: celebrations, confetti and small conveniences (no dependencies).
(function () {
  "use strict";
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // --- confetti -------------------------------------------------------------
  function confetti(amount) {
    if (reduced) return;
    const canvas = document.createElement("canvas");
    canvas.id = "confetti";
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = innerWidth * dpr;
    canvas.height = innerHeight * dpr;
    ctx.scale(dpr, dpr);
    const colours = ["#6c4bf4", "#12b5a5", "#f5a524", "#ef4444", "#3b82f6", "#a26cff"];
    const parts = Array.from({ length: amount }, () => ({
      x: innerWidth / 2 + (Math.random() - 0.5) * innerWidth * 0.4,
      y: innerHeight * 0.35,
      vx: (Math.random() - 0.5) * 14,
      vy: -Math.random() * 15 - 4,
      size: 5 + Math.random() * 7,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.35,
      colour: colours[Math.floor(Math.random() * colours.length)],
    }));
    let frame = 0;
    (function tick() {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      for (const p of parts) {
        p.vy += 0.42;
        p.vx *= 0.99;
        p.x += p.vx;
        p.y += p.vy;
        p.rot += p.vr;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.colour;
        ctx.fillRect(-p.size / 2, -p.size / 3, p.size, p.size / 1.6);
        ctx.restore();
      }
      if (++frame < 170) requestAnimationFrame(tick);
      else canvas.remove();
    })();
  }

  // --- toasts ---------------------------------------------------------------
  function toast(data) {
    const box = document.querySelector(".toasts");
    const el = document.createElement("div");
    el.className = "toast " + (data.kind || "success");
    let html = "";
    if (data.events && data.events.length) {
      const total = data.points;
      html += `<div class="total ${total < 0 ? "neg" : ""}" data-count="${total}">${total >= 0 ? "+" : ""}0 XP</div>`;
    }
    html += `<div>${escapeHtml(data.message || "")}</div>`;
    if (data.events && data.events.length) {
      html += "<ul>" + data.events.map((e, i) =>
        `<li style="animation-delay:${0.35 + i * 0.12}s"><span>${e.icon}</span><span>${escapeHtml(e.reason)}</span>` +
        `<span class="pts ${e.points < 0 ? "neg" : "pos"}">${e.points > 0 ? "+" : ""}${e.points}</span></li>`
      ).join("") + "</ul>";
    }
    el.innerHTML = html;
    box.appendChild(el);
    const counter = el.querySelector("[data-count]");
    if (counter) countUp(counter, Number(counter.dataset.count));
    const life = data.events && data.events.length ? 9000 : 5000;
    setTimeout(() => { el.classList.add("hide"); setTimeout(() => el.remove(), 450); }, life);
    el.addEventListener("click", () => el.remove());
  }

  function countUp(el, target) {
    if (reduced) { el.textContent = `${target >= 0 ? "+" : ""}${target} XP`; return; }
    const start = performance.now();
    const duration = 900;
    (function step(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = Math.round(target * eased);
      el.textContent = `${value >= 0 ? "+" : ""}${value} XP`;
      if (t < 1) requestAnimationFrame(step);
    })(start);
  }

  function modal(icon, title, text) {
    const overlay = document.createElement("div");
    overlay.className = "overlay show";
    overlay.innerHTML = `<div class="modal"><span class="big">${icon}</span><h2>${escapeHtml(title)}</h2>` +
      `<p>${escapeHtml(text)}</p><button class="primary" type="button">Awesome!</button></div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.querySelector("button").addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function celebrate(data) {
    toast(data);
    const big = data.level_up || (data.badges && data.badges.length) || data.quest;
    if (data.level_up) {
      confetti(260);
      setTimeout(() => modal(data.level_up.icon, `Level ${data.level_up.number}!`,
        `You are now a ${data.level_up.title}. Keep reading!`), 500);
    } else if (data.badges && data.badges.length) {
      confetti(180);
      const b = data.badges[0];
      setTimeout(() => modal(b.icon, `Badge unlocked: ${b.name}`, b.description), 500);
    } else if (data.quest) {
      confetti(160);
    } else if (data.points > 0 || data.sparkle) {
      confetti(big ? 120 : 60);
    }
    if (data.points) {
      document.querySelectorAll(".player .xp-value").forEach((el) => el.classList.add("pop"));
    }
  }

  // --- page setup -------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    const payload = document.getElementById("celebration");
    if (payload) {
      try {
        const data = JSON.parse(payload.textContent);
        if (data) celebrate(data);
      } catch (e) { /* ignore broken payloads */ }
      // drop the token from the address bar so a reload does not replay it
      const url = new URL(location.href);
      if (url.searchParams.has("c")) {
        url.searchParams.delete("c");
        history.replaceState(null, "", url);
      }
    }

    // animated progress rings and bars
    requestAnimationFrame(() => {
      document.querySelectorAll("[data-ring]").forEach((circle) => {
        const r = Number(circle.getAttribute("r"));
        const len = 2 * Math.PI * r;
        circle.style.strokeDasharray = len;
        circle.style.strokeDashoffset = len;
        requestAnimationFrame(() => {
          circle.style.strokeDashoffset = len * (1 - Number(circle.dataset.ring));
        });
      });
      document.querySelectorAll("[data-width]").forEach((el) => { el.style.width = el.dataset.width; });
    });

    // confirmations (e.g. penalties)
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
      form.addEventListener("submit", (e) => {
        if (!confirm(form.dataset.confirm)) e.preventDefault();
      });
    });

    // loading overlay for slow actions
    document.querySelectorAll("form[data-loading]").forEach((form) => {
      form.addEventListener("submit", () => {
        const overlay = document.getElementById("loading");
        overlay.querySelector("p").textContent = form.dataset.loading;
        overlay.classList.add("show");
      });
    });

    // toggles for inline forms
    document.querySelectorAll("[data-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = document.getElementById(button.dataset.toggle);
        target.hidden = !target.hidden;
        if (!target.hidden) {
          const first = target.querySelector("input, textarea");
          if (first) first.focus();
        }
      });
    });

    // topic editors (settings, recommendations): add/remove topic rows
    document.querySelectorAll(".topics-editor").forEach((editor) => {
      const list = editor.querySelector(".topic-list");
      const template = editor.querySelector("template");
      editor.querySelector("[data-add-topic]").addEventListener("click", () => {
        list.appendChild(template.content.cloneNode(true));
      });
      list.addEventListener("click", (e) => {
        if (e.target.matches("[data-remove-topic]")) e.target.closest(".topic-row").remove();
      });
    });

    // links to a collapsed section (#how) open it
    const openTarget = () => {
      const target = location.hash && document.getElementById(location.hash.slice(1));
      if (target && target.tagName === "DETAILS") target.open = true;
    };
    openTarget();
    window.addEventListener("hashchange", openTarget);

    const weights = document.querySelectorAll("[data-weight]");
    const share = () => {
      const values = Array.from(weights).map((w) => Number(w.value) || 0);
      const sum = values.reduce((a, b) => a + b, 0) || 1;
      weights.forEach((w, i) => {
        const out = document.getElementById(w.dataset.weight);
        if (out) out.textContent = `${Math.round((values[i] / sum) * 100)} % of the score`;
      });
    };
    weights.forEach((w) => w.addEventListener("input", share));
    if (weights.length) share();

    // keyboard: "n" jumps to the queue's top paper, "r" to recommendations
    document.addEventListener("keydown", (e) => {
      if (e.target.matches("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "n") location.href = "/queue#top";
      if (e.key === "r") location.href = "/recommendations";
      if (e.key === "p") location.href = "/progress";
    });
  });
})();
