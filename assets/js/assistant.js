/* assistant.js - Washington Gas mock site: glue between the pages and the Google CX Agent Studio
   web widget (<chat-messenger>, SDK v1.16), plus the graphical chat cards.

   1. Page context   window.wglPageContext (from <body data-page-context>) is written to sessionStorage
                     "chat-messenger-variables" as {page_context} BEFORE the widget opens (the SDK reads it on
                     every request, including the welcome event) and re-applied on session events.
   2. Launcher       every [data-open-assistant] element opens the widget; placeholder launcher fallback.
   3. Cards          custom elements <cx-show-...> (one per card tool, same renderer; shadow DOM, inline SVG, no libraries)
                     rendered inside the chat message list by the SDK from the rich element the platform sends
                     for a CUSTOM widget tool: {type: custom_template, name: cx-<tool-name>, payload: <card fields>}.
                     <wgl-card> ({tool, args}) is kept for the preview page and the console.
   4. Welcome        the WEBSITE greets the visitor when the chat opens (no agent call, no welcome event): a warm
                     page-aware greeting (renderCustomText) and a chips card with an English | Espanol toggle
                     (<cx-show-welcome-chips>). Only a chip tap or typed text invokes the agent, with the widget variables
                     {page_context, ui_language}. Texts: WELCOME below, mirrored from seed/data/verbatim_strings.json
                     greeting_page_* and the chip lists of the before_model callback (an offline test keeps the three equal).
   Contract: docs/widget-contract.md. Plain JavaScript, no dependencies.
   Preview mode: set window.WGL_PREVIEW = true before this script (widget-preview.html) to skip all SDK wiring. */
(function () {
  "use strict";
  var doc = document;
  var root = doc.documentElement;
  var PREVIEW = !!window.WGL_PREVIEW;
  var VARS_KEY = "chat-messenger-variables";
  var fab = null;
  var toast = null;
  var toastTimer = null;

  /* ---------------------------------------------------------------- 1. page context */
  window.wglPageContext = (doc.body && doc.body.getAttribute("data-page-context")) || "";

  function chatEl() { return doc.querySelector("chat-messenger"); }

  /* ---- UI language chosen with the welcome toggle (en | es): kept for the browser session ---- */
  var LANG_KEY = "wgl-ui-language";
  function pageKey() {
    var p = window.wglPageContext || "home";
    return WELCOME && WELCOME.pages[p] ? p : "home";
  }
  function defaultLang(page) { return page === "spanish" ? "es" : "en"; }
  function readLang(page) {
    try {
      var o = JSON.parse(window.sessionStorage.getItem(LANG_KEY) || "null");
      /* a choice made elsewhere never overrides the Spanish page's own default */
      if (o && (o.l === "en" || o.l === "es") && (page !== "spanish" || o.p === "spanish")) { return o.l; }
    } catch (e) { /* storage blocked */ }
    return defaultLang(page);
  }
  function writeLang(lang, page) {
    try { window.sessionStorage.setItem(LANG_KEY, JSON.stringify({ l: lang, p: page })); } catch (e) { /* storage blocked */ }
  }

  /* Merge values into the widget variables (sessionStorage "chat-messenger-variables", read by the SDK on every request). */
  function setVars(extra) {
    var obj = {};
    try {
      var raw = window.sessionStorage.getItem(VARS_KEY);
      if (raw) { obj = JSON.parse(raw) || {}; }
    } catch (e) { obj = {}; }
    var changed = false;
    Object.keys(extra).forEach(function (k) { if (obj[k] !== extra[k]) { obj[k] = extra[k]; changed = true; } });
    if (changed) {
      try { window.sessionStorage.setItem(VARS_KEY, JSON.stringify(obj)); } catch (e2) { /* storage blocked */ }
    }
    var el = chatEl();
    if (!PREVIEW && el && typeof el.setVariables === "function") {
      try { el.setVariables(obj); } catch (e3) { /* presenter not ready yet; storage already holds it */ }
    }
    return obj;
  }

  function applyVariables() {
    var ctx = window.wglPageContext;
    if (PREVIEW || !ctx) { return; }
    setVars({ page_context: ctx, ui_language: readLang(ctx) });
  }
  applyVariables();   /* synchronous, runs before the deferred SDK script starts */

  /* ---------------------------------------------------------------- 4. welcome texts
     ONE data structure for the site-rendered welcome (EN and ES per page). Mirrors seed/data/verbatim_strings.json
     greeting_page_* and the _PAGE_CHIPS lists of the before_model callback; evals/offline_tests/test_site_welcome.py
     fails when the three drift apart. "notfound" exists on the site only (the callback fallback has no such page). */
  /* WELCOME-DATA-START */
  var WELCOME = {
    "prompt": {
      "en": "What would you like to do?",
      "es": "¿Qué le gustaría hacer?"
    },
    "pages": {
      "home": {
        "en": {
          "greeting": "Welcome to Washington Gas. How can I help you today?",
          "chips": [
            "Pay my bill",
            "Start or stop service",
            "Report a gas smell",
            "Check an outage",
            "Help with my payment"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. ¿En qué puedo ayudarle hoy?",
          "chips": [
            "Pagar mi factura",
            "Iniciar o detener el servicio",
            "Reportar olor a gas",
            "Consultar un corte de servicio",
            "Ayuda con mi pago"
          ]
        }
      },
      "billing": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our billing page. How can I help with your bill today?",
          "chips": [
            "Pay my bill",
            "Why is my bill high?",
            "Set up AutoPay",
            "Payment plans and help"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de facturación. ¿En qué puedo ayudarle con su factura hoy?",
          "chips": [
            "Pagar mi factura",
            "¿Por qué mi factura es alta?",
            "Configurar AutoPay",
            "Planes de pago y ayuda"
          ]
        }
      },
      "service": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our service page. How can I help with your service today?",
          "chips": [
            "Start service",
            "Stop service",
            "I am moving",
            "Is gas available at my address"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de servicio. ¿En qué puedo ayudarle con su servicio hoy?",
          "chips": [
            "Iniciar servicio",
            "Detener servicio",
            "Me estoy mudando",
            "¿Hay gas disponible en mi dirección?"
          ]
        }
      },
      "safety": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our safety page. If you smell gas, leave the area right away, call 911, then Washington Gas at 844-WASHGAS, 844-927-4427, option 1. Tap Report a gas smell for help.",
          "chips": [
            "Report a gas smell",
            "My gas is off",
            "Carbon monoxide alarm",
            "Call before you dig (811)"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de seguridad. Si huele a gas, salga del área de inmediato y llame al 911 y luego a Washington Gas al 844-WASHGAS, 844-927-4427, opción 1. Toque Reportar olor a gas para recibir ayuda.",
          "chips": [
            "Reportar olor a gas",
            "Mi gas está apagado",
            "Alarma de monóxido de carbono",
            "Llame antes de excavar (811)"
          ]
        }
      },
      "assistance": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our assistance page. I can help you find ways to manage your bill. What would you like to know?",
          "chips": [
            "Help paying my bill",
            "Payment plans",
            "Energy assistance programs",
            "Medical or special needs"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de asistencia. Puedo ayudarle a encontrar formas de manejar su factura. ¿Qué le gustaría saber?",
          "chips": [
            "Ayuda para pagar mi factura",
            "Planes de pago",
            "Programas de asistencia de energía",
            "Necesidades médicas o especiales"
          ]
        }
      },
      "savings": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our savings page. How can I help you save today?",
          "chips": [
            "Rebates and how to claim",
            "Home energy assessment",
            "Budget Billing"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de ahorros. ¿Cómo puedo ayudarle a ahorrar hoy?",
          "chips": [
            "Reembolsos y cómo solicitarlos",
            "Evaluación de energía del hogar",
            "Facturación presupuestada"
          ]
        }
      },
      "business": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our business page. How can I help with your business account today?",
          "chips": [
            "Start service for my business",
            "Pay my bill",
            "Added load or split meter",
            "Service abandonment"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página para negocios. ¿En qué puedo ayudarle con su cuenta comercial hoy?",
          "chips": [
            "Iniciar servicio para mi negocio",
            "Pagar mi factura",
            "Carga adicional o medidor dividido",
            "Abandono de servicio"
          ]
        }
      },
      "choice": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our Customer Choice page. What would you like to know?",
          "chips": [
            "How Customer Choice works",
            "Pay my bill",
            "Start or stop service"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de Customer Choice. ¿Qué le gustaría saber?",
          "chips": [
            "Cómo funciona Customer Choice",
            "Pagar mi factura",
            "Iniciar o detener el servicio"
          ]
        }
      },
      "claims": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our claims page. How can I help?",
          "chips": [
            "Report damage",
            "Safety information",
            "Talk to a person"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de reclamos. ¿En qué puedo ayudarle?",
          "chips": [
            "Reportar daños",
            "Información de seguridad",
            "Hablar con una persona"
          ]
        }
      },
      "energy": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our energy efficiency page. How can I help you use less energy today?",
          "chips": [
            "Home energy assessment",
            "Budget Billing",
            "Rebates"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de eficiencia energética. ¿Cómo puedo ayudarle a usar menos energía hoy?",
          "chips": [
            "Evaluación de energía del hogar",
            "Facturación presupuestada",
            "Reembolsos"
          ]
        }
      },
      "contact": {
        "en": {
          "greeting": "Welcome to Washington Gas. I see you are on our contact page. How can I help you today?",
          "chips": [
            "Pay my bill",
            "Report a gas smell",
            "Talk to a person"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Veo que está en nuestra página de contacto. ¿En qué puedo ayudarle hoy?",
          "chips": [
            "Pagar mi factura",
            "Reportar olor a gas",
            "Hablar con una persona"
          ]
        }
      },
      "spanish": {
        "en": {
          "greeting": "Welcome to Washington Gas. I can help you in Spanish. How can I help you today?",
          "chips": [
            "Pay my bill",
            "Start or stop service",
            "Report a gas smell",
            "Check an outage",
            "Help with my payment"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. Con gusto le ayudo en español. ¿En qué puedo ayudarle hoy?",
          "chips": [
            "Pagar mi factura",
            "Iniciar o detener el servicio",
            "Reportar olor a gas",
            "Consultar un corte de servicio",
            "Ayuda con mi pago"
          ]
        }
      },
      "notfound": {
        "en": {
          "greeting": "Welcome to Washington Gas. I could not find that page, but I can still help. What would you like to do?",
          "chips": [
            "Pay my bill",
            "Start or stop service",
            "Report a gas smell",
            "Check an outage",
            "Help with my payment"
          ]
        },
        "es": {
          "greeting": "Bienvenido a Washington Gas. No encontré esa página, pero aún puedo ayudarle. ¿Qué le gustaría hacer?",
          "chips": [
            "Pagar mi factura",
            "Iniciar o detener el servicio",
            "Reportar olor a gas",
            "Consultar un corte de servicio",
            "Ayuda con mi pago"
          ]
        }
      }
    }
  };
  /* WELCOME-DATA-END */

  /* ---------------------------------------------------------------- 2. launcher */
  function widgetPresent() {
    return !!(window.customElements && customElements.get("chat-messenger")) ||
           !!doc.querySelector("chat-messenger-chat-bubble");
  }
  function syncPlaceholder() {
    if (widgetPresent()) { root.classList.add("has-assistant-widget"); }
  }
  function findLauncher() {
    var host = chatEl();
    if (!host) { return null; }
    var bubble = host.querySelector("chat-messenger-chat-bubble") ||
                 (host.shadowRoot && host.shadowRoot.querySelector("chat-messenger-chat-bubble"));
    if (!bubble) { return null; }
    var sr = bubble.shadowRoot;
    var btn = sr && (sr.querySelector(".bubble") || sr.querySelector("button, [role=button]"));
    return { bubble: bubble, button: btn || null };
  }
  function isAlreadyOpen(l) {
    return !!(l && l.bubble && l.bubble.classList.contains("hidden"));
  }
  function showToast(msg) {
    if (!toast) { return; }
    toast.textContent = msg;
    toast.classList.add("is-shown");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove("is-shown"); }, 6000);
  }
  function fallback() {
    if (!fab) { return; }
    fab.scrollIntoView({ block: "nearest", behavior: "smooth" });
    fab.classList.remove("pulse");
    void fab.offsetWidth;
    fab.classList.add("pulse");
    showToast("The assistant widget is not connected on this page yet. Paste the embed code into assets/js/widget-embed.html and run scripts/build_pages.py.");
  }
  function openAssistant(attempt) {
    attempt = attempt || 0;
    var l = findLauncher();
    if (l && l.bubble && "expanded" in l.bubble && l.bubble.shadowRoot) {
      l.bubble.expanded = true;
      return;
    }
    if (l && isAlreadyOpen(l)) { return; }
    if (l && l.button) { l.button.click(); return; }
    if (l && l.bubble && typeof l.bubble.click === "function") { l.bubble.click(); return; }
    if ((chatEl() && attempt < 16) &&
        (window.customElements && customElements.get("chat-messenger") || attempt < 8)) {
      setTimeout(function () { openAssistant(attempt + 1); }, 250);
      return;
    }
    fallback();
  }

  /* ---------------------------------------------------------------- 3. cards */
  var BRAND = { navy: "#002a5c", blue: "#0072bc", gold: "#ffc72c" };
  var DONUT_COLORS = ["#002a5c", "#0072bc", "#4eaf6b", "#b45309", "#6b4fa3", "#5b6770"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function esc(v) {
    return String(v === undefined || v === null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function money(v) {
    var n = num(v);
    var s = Math.abs(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return (n < 0 ? "-$" : "$") + s;
  }
  function plain(v) { var n = num(v); return (Math.round(n * 10) / 10).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function shortLabel(p) {
    var s = String(p === undefined || p === null ? "" : p).trim();
    var m = s.match(/^[A-Za-z]{3,}/);
    if (m) { return m[0].slice(0, 3); }
    m = s.match(/^(\d{4})-(\d{2})/);
    if (m) { return MONTHS[Number(m[2]) - 1] || s.slice(0, 3); }
    return s.slice(0, 3);
  }
  function arr(v, max) { return Array.isArray(v) ? v.slice(0, max || 50) : []; }
  function row(label, value) {
    return value === undefined || value === null || value === "" ? "" :
      '<div class="kv"><span class="k">' + esc(label) + '</span><span class="v">' + esc(value) + "</span></div>";
  }
  function head(title, sub, icon) {
    return '<div class="card-head">' + (icon ? '<span class="ico" aria-hidden="true">' + icon + "</span>" : "") +
      '<div class="ht"><div class="title">' + esc(title) + "</div>" + (sub ? '<div class="sub">' + esc(sub) + "</div>" : "") + "</div></div>";
  }
  function empty(msg) { return '<p class="empty">' + esc(msg || "This information is not available right now.") + "</p>"; }
  /* Rev 29b: the short bubble above a card (display_text) is the only place its sentence appears. A card heading, hint or note that
     repeats a sentence of that bubble (three or more words, or the whole bubble) is replaced by the card's own default. */
  function nrm(s) { return String(s === undefined || s === null ? "" : s).replace(/\*\*/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/^ +| +$/g, ""); }
  function ttlOf(a, def) { return a.title && !dupOf(a.title, a.display_text) ? a.title : def; }
  function dupOf(text, display) {
    var n = nrm(text), d = nrm(display);
    if (!n || !d) { return false; }
    return n === d || (n.split(" ").length >= 3 && (" " + d + " ").indexOf(" " + n + " ") > -1);
  }

  var ICON = {
    phone: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M6.600 10.800a15 15 0 0 0 6.600 6.600l2.200-2.200a1 1 0 0 1 1-.25 11.400 11.400 0 0 0 3.600.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.500a1 1 0 0 1 1 1c0 1.250.2 2.450.57 3.600a1 1 0 0 1-.25 1z"/></svg>',
    chart: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M4 20V10h4v10H4zm6 0V4h4v16h-4zm6 0v-7h4v7h-4z"/></svg>',
    flame: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12.5 2c.4 3.200-1.700 5-3.300 6.900C7.700 10.700 6.800 12.400 6.800 14.400a5.400 5.400 0 0 0 10.800 0c0-2.400-1.300-4.300-2.600-5.500-.1 1.400-.8 2.300-1.700 2.800.5-3.400-.3-6.700-.8-9.700z"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="22" height="22"><path fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M5 12.500l4.500 4.500L19 7.500"/></svg>',
    cal: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2H7zm-2 8h14v9H5v-9z"/></svg>',
    alert: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 2 1 21h22L12 2zm-1 7h2v6h-2V9zm0 8h2v2h-2v-2z"/></svg>',
    tag: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M3 12V3h9l9 9-9 9-9-9zm5-5a1.500 1.500 0 1 0 0 3 1.500 1.500 0 0 0 0-3z"/></svg>',
    cloud: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M6.500 19a4.500 4.500 0 0 1-.4-9A6 6 0 0 1 17.600 8.500 4.800 4.800 0 0 1 17.500 19h-11z"/></svg>',
    dollar: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15.500V19h-2v-1.500c-1.500-.3-2.700-1.200-2.800-2.800h2c.1.700.8 1.200 1.900 1.200 1 0 1.700-.4 1.700-1.100 0-.7-.5-1-2-1.400-1.900-.5-3.400-1.200-3.400-3.100 0-1.400 1.100-2.400 2.600-2.700V5h2v1.700c1.400.3 2.400 1.300 2.500 2.700h-2c-.1-.7-.7-1.100-1.700-1.100-.9 0-1.500.4-1.500 1 0 .6.5.9 2 1.300 1.800.5 3.500 1.100 3.500 3.200 0 1.500-1.200 2.500-2.800 2.700z"/></svg>'
  };

  /* Rev 23: icon names the options, compare and checklist cards accept (SVG only, never emoji characters) */
  var OICON = {
    card: '<svg viewBox="0 0 24 24" width="20" height="20"><rect x="2.500" y="5" width="19" height="14" rx="2.500" fill="none" stroke="currentColor" stroke-width="2"/><path d="M2.500 10h19" stroke="currentColor" stroke-width="2"/><path d="M6 15h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    bank: '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 3 2.500 8.500h19L12 3z" fill="currentColor"/><path d="M5 11v7M10 11v7M14 11v7M19 11v7M3 20.500h18" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>',
    calendar: ICON.cal,
    home: '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 3 2.500 11H5v9h5v-6h4v6h5v-9h2.500L12 3z" fill="currentColor"/></svg>',
    flame: ICON.flame,
    check: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" d="M5 12.500l4.500 4.500L19 7.500"/></svg>',
    money: ICON.dollar,
    info: '<svg viewBox="0 0 24 24" width="20" height="20"><circle cx="12" cy="12" r="9.500" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v6M12 7.500v.2" stroke="currentColor" stroke-width="2.200" stroke-linecap="round"/></svg>',
    clock: '<svg viewBox="0 0 24 24" width="20" height="20"><circle cx="12" cy="12" r="9.500" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7v5.500l3.500 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    phone: '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M6.600 10.800a15 15 0 0 0 6.600 6.600l2.200-2.200a1 1 0 0 1 1-.25 11.400 11.400 0 0 0 3.600.6 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.500a1 1 0 0 1 1 1c0 1.300.2 2.500.6 3.600a1 1 0 0 1-.25 1l-2.250 2.200z"/></svg>'
  };
  function oicon(name) { return OICON[name] || OICON.check; }

  /* ---- chart builders (inline SVG) ---- */
  function svgOpen(w, h, label) {
    return '<svg class="chart" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label) + '" preserveAspectRatio="xMidYMid meet">';
  }
  function barChart(o) {
    var vals = o.values, n = vals.length;
    if (!n) { return empty(); }
    var W = 320, H = 150, pl = 8, pr = 8, pt = 24, pb = 22, base = H - pb;
    var max = Math.max.apply(null, vals); if (max <= 0) { max = 1; }
    var slot = (W - pl - pr) / n, bw = Math.min(24, slot * 0.62);
    var maxI = vals.indexOf(Math.max.apply(null, vals));
    var s = svgOpen(W, H, o.aria);
    s += '<line x1="' + pl + '" y1="' + base + '" x2="' + (W - pr) + '" y2="' + base + '" stroke="#9fb6cc" stroke-width="1"/>';
    for (var i = 0; i < n; i++) {
      var h = Math.max(2, (vals[i] / max) * (base - pt));
      var x = pl + slot * i + (slot - bw) / 2, y = base - h;
      var cur = i === o.cur;
      s += '<g class="bar" style="animation-delay:' + (i * 35) + 'ms"><title>' + esc(o.labels[i] + ": " + o.fmt(vals[i])) + "</title>" +
        '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="3" fill="' + (cur ? BRAND.navy : BRAND.blue) + '"/>' +
        (cur ? '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="4" rx="2" fill="' + BRAND.gold + '"/>' : "") + "</g>";
      s += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle" class="ax' + (cur ? " cur" : "") + '">' + esc(shortLabel(o.labels[i])) + "</text>";
      if (cur || i === maxI) {
        s += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (y - 5).toFixed(1) + '" text-anchor="middle" class="val' + (cur ? " cur" : "") + '">' + esc(o.fmt(vals[i])) + "</text>";
      }
    }
    return s + "</svg>";
  }
  function weatherChart(months, unit) {
    var n = months.length;
    if (!n) { return empty(); }
    var W = 320, H = 170, pl = 8, pr = 8, pt = 26, pb = 22, base = H - pb;
    var uMax = Math.max.apply(null, months.map(function (m) { return num(m.usage); })) || 1;
    var hMax = Math.max.apply(null, months.map(function (m) { return num(m.hdd); })) || 1;
    var slot = (W - pl - pr) / n, bw = Math.min(22, slot * 0.6);
    var s = svgOpen(W, H, "Usage in " + unit + " compared with heating degree days by month: " +
      months.map(function (m) { return m.period + " " + plain(m.usage) + " " + unit + ", " + plain(m.hdd) + " degree days"; }).join("; "));
    s += '<line x1="' + pl + '" y1="' + base + '" x2="' + (W - pr) + '" y2="' + base + '" stroke="#9fb6cc"/>';
    var pts = [];
    months.forEach(function (m, i) {
      var h = Math.max(2, (num(m.usage) / uMax) * (base - pt));
      var x = pl + slot * i + (slot - bw) / 2, y = base - h, cx = x + bw / 2;
      s += '<g class="bar" style="animation-delay:' + (i * 35) + 'ms"><title>' + esc(m.period + ": " + plain(m.usage) + " " + unit + ", " + plain(m.hdd) + " HDD") + "</title>" +
        '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="3" fill="' + BRAND.blue + '"/></g>';
      s += '<text x="' + cx.toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle" class="ax">' + esc(shortLabel(m.period)) + "</text>";
      pts.push([cx, base - (num(m.hdd) / hMax) * (base - pt)]);
    });
    var d = pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    s += '<path class="line" d="' + d + '" fill="none" stroke="#b45309" stroke-width="2.500" stroke-linejoin="round" stroke-linecap="round"/>';
    pts.forEach(function (p) { s += '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3" fill="#fff" stroke="#b45309" stroke-width="2"/>'; });
    return s + "</svg>";
  }
  function donut(items, total, aria) {
    var R = 46, C = 2 * Math.PI * R, sum = items.reduce(function (a, b) { return a + Math.max(0, b.amount); }, 0) || 1;
    var s = svgOpen(140, 140, aria) + '<g transform="rotate(-90 70 70)"><circle cx="70" cy="70" r="' + R + '" fill="none" stroke="#e6eef5" stroke-width="18"/>';
    var off = 0;
    items.forEach(function (it, i) {
      var len = (Math.max(0, it.amount) / sum) * C;
      s += '<circle class="seg" cx="70" cy="70" r="' + R + '" fill="none" stroke="' + DONUT_COLORS[i % DONUT_COLORS.length] +
        '" stroke-width="18" stroke-dasharray="' + Math.max(0, len - 1.5).toFixed(2) + " " + (C - Math.max(0, len - 1.5)).toFixed(2) +
        '" stroke-dashoffset="' + (-off).toFixed(2) + '"><title>' + esc(it.label + ": " + money(it.amount)) + "</title></circle>";
      off += len;
    });
    s += '</g><text x="70" y="66" text-anchor="middle" class="dl">Total</text><text x="70" y="86" text-anchor="middle" class="dv">' + esc(money(total)) + "</text></svg>";
    return s;
  }
  function stars(prompt) {
    var s = "";
    for (var i = 1; i <= 5; i++) {
      s += '<button type="button" class="star" data-send="I rate this experience ' + i + ' out of 5" aria-label="' + i + ' out of 5">' +
        '<svg viewBox="0 0 24 24" width="30" height="30"><path d="M12 2.500l2.900 6.200 6.700.8-5 4.600 1.400 6.700L12 17.400 6 20.800l1.400-6.700-5-4.600 6.700-.8L12 2.500z" fill="currentColor"/></svg></button>';
    }
    return s;
  }
  function actionBtn(label, msg, cls) {
    return label && msg ? '<button type="button" class="btn ' + (cls || "") + '" data-send="' + esc(msg) + '">' + esc(label) + "</button>" : "";
  }

  /* severity -> badge text, class, glyph (never colour alone) */
  var SEV = {
    info: { t: "Good to know", g: "i" },
    attention: { t: "Needs attention", g: "!" },
    urgent: { t: "Urgent", g: "!" },
    saving: { t: "Saving", g: "$" }
  };

  function carousel(slides, label) {
    var s = '<div class="car" role="group" aria-roledescription="carousel" aria-label="' + esc(label) + '">' +
      '<div class="track" tabindex="0">' + slides.map(function (h, i) {
        return '<div class="slide" role="group" aria-roledescription="slide" aria-label="' + (i + 1) + " of " + slides.length + '">' + h + "</div>";
      }).join("") + "</div>";
    if (slides.length > 1) {
      s += '<button type="button" class="nav prev" data-nav="-1" aria-label="Previous">&#8249;</button><button type="button" class="nav next" data-nav="1" aria-label="Next">&#8250;</button>' +
        '<div class="dots" aria-hidden="true">' + slides.map(function (_, i) { return '<i class="' + (i ? "" : "on") + '"></i>'; }).join("") + "</div>";
    }
    return s + "</div>";
  }

  /* ---- card definitions: tool name -> function(args) -> HTML ---- */
  var CARDS = {
    show_usage_history_card: function (a) {
      var ms = arr(a.months, 13), unit = a.unit || "units";
      if (!ms.length) { return head("Your usage", "", ICON.flame) + empty(); }
      var vals = ms.map(function (m) { return num(m.usage); });
      var hi = vals.indexOf(Math.max.apply(null, vals)), lo = vals.indexOf(Math.min.apply(null, vals));
      return head("Your gas usage", "Last " + ms.length + " months, in " + unit, ICON.flame) +
        barChart({ values: vals, labels: ms.map(function (m) { return m.period; }), cur: ms.length - 1, fmt: plain,
          aria: "Gas usage in " + unit + " by month: " + ms.map(function (m) { return m.period + " " + plain(m.usage); }).join(", ") }) +
        '<div class="stats"><div><span class="k">Highest</span><b>' + esc(shortLabel(ms[hi].period)) + " " + esc(plain(vals[hi])) + '</b></div>' +
        '<div><span class="k">Lowest</span><b>' + esc(shortLabel(ms[lo].period)) + " " + esc(plain(vals[lo])) + "</b></div></div>";
    },
    show_bill_trend_card: function (a) {
      var ms = arr(a.months, 13);
      if (!ms.length) { return head("Your bill by month", "", ICON.chart) + empty(); }
      var cur = -1;
      ms.forEach(function (m, i) { if (m.is_current) { cur = i; } });
      if (cur < 0) { cur = ms.length - 1; }
      var chipList = (a.action_label && a.action_message ? [{ label: a.action_label, message: a.action_message }] : [])
        .concat(arr(a.extra_actions, 2).filter(function (x) { return x && x.label && x.message; }));
      var follow = chipList.length ?
        '<div class="chips follow" role="group" aria-label="Next step" data-lock="1">' + chipList.map(function (c) {
          return '<button type="button" class="chip" data-send="' + esc(c.message) + '">' + esc(c.label) + "</button>";
        }).join("") + "</div>" : "";
      /* Rev 25: optional reasons (every factor of the change with its amount); nothing is drawn when it is absent */
      var rs = arr(a.reasons, 6).filter(function (r) { return r && r.label; });
      var reasons = rs.length ?
        '<ul class="rsn" aria-label="Why the bill changed">' + rs.map(function (r) {
          var up = r.direction === "up", down = r.direction === "down";
          var amt = r.amount === undefined || r.amount === null ? "" : (up ? "+" : down ? "-" : "") + money(Math.abs(num(r.amount)));
          return '<li><span class="rico" aria-hidden="true">' + oicon(r.icon) + '</span><span class="rl">' + esc(r.label) + "</span>" +
            (amt ? '<b class="ra' + (up ? " up" : down ? " dn" : "") + '">' + esc(amt) + "</b>" : "") + "</li>";
        }).join("") + "</ul>" : "";
      return head("Your bill, month by month", "Amount billed each month", ICON.chart) +
        barChart({ values: ms.map(function (m) { return num(m.amount); }), labels: ms.map(function (m) { return m.period; }), cur: cur, fmt: money,
          aria: "Monthly bill amounts: " + ms.map(function (m) { return m.period + " " + money(m.amount); }).join(", ") }) +
        (a.highlight_note && !dupOf(a.highlight_note, a.display_text) ? '<p class="note"><span aria-hidden="true">&#9679;</span> ' + esc(a.highlight_note) + "</p>" : "") + reasons + follow;
    },
    show_weather_usage_card: function (a) {
      var ms = arr(a.months, 13), unit = a.unit || "units";
      if (!ms.length) { return head("Usage and weather", "", ICON.cloud) + empty(); }
      return head("Usage vs. the weather", "Colder months need more heat", ICON.cloud) + weatherChart(ms, unit) +
        '<div class="legend"><span><i class="sw bar"></i>Gas used (' + esc(unit) + ')</span><span><i class="sw ln"></i>Heating degree days</span></div>' +
        (arr(a.stats, 3).length ? '<div class="stats wstats">' + arr(a.stats, 3).map(function (t) {
          return '<div><span class="k">' + esc(t && t.label) + "</span><b>" + esc(t && t.value) + "</b></div>";
        }).join("") + "</div>" : "");
    },
    show_bill_breakdown_card: function (a) {
      var items = arr(a.items, 40).map(function (i) { return { label: i.label, amount: num(i.amount) }; });
      if (!items.length) { return head("Bill breakdown", "", ICON.dollar) + empty(); }
      if (items.length > 6) {
        var rest = items.slice(5).reduce(function (x, y) { return x + y.amount; }, 0);
        items = items.slice(0, 5).concat([{ label: "Other", amount: rest }]);
      }
      var total = a.total !== undefined && a.total !== null ? num(a.total) : items.reduce(function (x, y) { return x + y.amount; }, 0);
      return head("Where your money goes", "This bill", ICON.dollar) + '<div class="donut-row">' +
        donut(items, total, "Bill breakdown: " + items.map(function (i) { return i.label + " " + money(i.amount); }).join(", ") + ". Total " + money(total)) +
        '<ul class="lg">' + items.map(function (it, i) {
          return '<li><i class="sw" style="background:' + DONUT_COLORS[i % DONUT_COLORS.length] + '"></i><span class="lb">' + esc(it.label) +
            '</span><b>' + esc(money(it.amount)) + "</b></li>";
        }).join("") + "</ul></div>";
    },
    show_insights_carousel: function (a) {
      var ins = arr(a.insights, 6);
      if (!ins.length) { return head("Insights for you", "", ICON.flame) + empty(); }
      return carousel(ins.map(function (i) {
        var sv = SEV[i.severity] ? i.severity : "info", d = SEV[sv];
        return '<div class="ins sev-' + sv + '"><span class="badge"><b aria-hidden="true">' + d.g + "</b> " + d.t + "</span>" +
          '<div class="title">' + esc(i.title) + '</div><p class="sum">' + esc(i.summary) + "</p>" +
          actionBtn(i.action_label, i.action_message, "") + "</div>";
      }), "Insights for you");
    },
    show_rebate_card: function (a) {
      var rs = arr(a.rebates, 6);
      if (!rs.length) { return head("Rebates for you", "", ICON.tag) + empty(); }
      return head("Rebates you may qualify for", rs.length + " match" + (rs.length > 1 ? "es" : ""), ICON.tag) +
        '<div class="stack">' + rs.map(function (r) {
          return '<div class="rb"><div class="rb-top"><div class="rb-name">' + esc(r.name) + '</div><div class="rb-amt">' + esc(r.amount) + "</div></div>" +
            (r.deadline ? '<div class="rb-dl"><span aria-hidden="true">&#9201;</span> Claim by ' + esc(r.deadline) + "</div>" : "") +
            (r.status_note ? '<div class="rb-note">' + esc(r.status_note) + "</div>" : "") +
            actionBtn(r.action_label, r.action_message, "") + "</div>";
        }).join("") + "</div>";
    },
    show_claim_status_card: function (a) {
      var st = arr(a.steps, 8);
      if (!st.length) { return head("Claim status", "", ICON.check) + empty(); }
      return head("Claim status", a.claim_id ? "Claim " + a.claim_id : "", ICON.check) + '<ol class="steps">' + st.map(function (s, i) {
        var state = s.state === "done" || s.state === "current" ? s.state : "todo";
        var word = state === "done" ? "Done" : state === "current" ? "In progress" : "Next";
        return '<li class="step ' + state + '"><span class="dot" aria-hidden="true">' + (state === "done" ? ICON.check : (i + 1)) + '</span>' +
          '<span class="sl">' + esc(s.label) + '</span><span class="sw2">' + word + "</span></li>";
      }).join("") + "</ol>" + (a.note ? '<p class="note">' + esc(a.note) + "</p>" : "");
    },
    show_payment_receipt_card: function (a) {
      return '<div class="receipt"><span class="okc" aria-hidden="true">' + ICON.check + '</span><div class="rt">Payment received</div>' +
        '<div class="ramt">' + esc(money(a.amount)) + "</div>" +
        '<div class="tear" aria-hidden="true"></div>' +
        row("Date", a.date) + row("Confirmation", a.confirmation) + row("Paid with", a.method_last4 ? "Card ending " + a.method_last4 : "") + "</div>";
    },
    show_bill_summary_card: function (a) {
      return head(ttlOf(a, "Bill summary"), "", ICON.dollar) + '<div class="ramt left">' + esc(money(a.amount)) + "</div>" +
        (a.convenience_fee ? row("Convenience fee", money(a.convenience_fee)) : "") + row("Due date", a.due_date) +
        row("Confirmation", a.confirmation_number) + row("Card", a.card_last4 ? "Ending " + a.card_last4 : "");
    },
    show_quick_actions: function (a) {
      var o = arr(a.options, 3);
      return '<div class="chips" role="group" aria-label="' + esc(a.prompt || "Quick answers") + '" data-lock="1">' + o.map(function (t) {
        return '<button type="button" class="chip" data-send="' + esc(t) + '">' + esc(t) + "</button>";
      }).join("") + "</div>";
    },
    show_welcome_chips: function (a) {
      var page = WELCOME.pages[a.page] ? a.page : "home";
      var lang = a.lang === "es" ? "es" : "en";
      var w = WELCOME.pages[page][lang];
      var off = a.sent ? " disabled" : "";
      var tog = function (code, label, name) {
        return '<button type="button" class="lang' + (lang === code ? " on" : "") + '" data-lang="' + code + '" lang="' + code + '" aria-pressed="' + (lang === code) +
          '" aria-label="' + name + '"' + off + ">" + label + "</button>";
      };
      /* the greeting bubble above is rendered by the SDK in the opening language: after a switch, the new-language greeting
         is repeated inside the card so the visitor reads the page in the language they picked */
      var note = lang !== a.initial ? '<p class="lnote" lang="' + lang + '">' + esc(w.greeting) + "</p>" : "";
      return '<div class="wel' + (a.sent ? " sent" : "") + '"><div class="langrow" role="group" aria-label="Language / Idioma"><span class="globe" aria-hidden="true">&#127760;</span>' +
        tog("en", "English", "English") + '<span class="sep" aria-hidden="true">|</span>' + tog("es", "Espa&ntilde;ol", "Espa&ntilde;ol") + "</div>" +
        '<div class="live" role="status" aria-live="polite">' + note + "</div>" +
        '<div class="chips" role="group" aria-label="' + esc(WELCOME.prompt[lang]) + '" data-lock="1">' + w.chips.map(function (t) {
          return '<button type="button" class="chip' + (a.picked === t ? " picked" : "") + '" data-send="' + esc(t) + '"' + off + ">" + esc(t) + "</button>";
        }).join("") + "</div></div>";
    },
    show_slot_picker: function (a) {
      var sl = arr(a.slots, 3);
      if (!sl.length) { return head("Choose a time", "", ICON.cal) + empty("No times are available right now."); }
      return head("Choose a time", a.service_type ? "Appointment: " + a.service_type : "", ICON.cal) + '<div class="slots" data-lock="1">' + sl.map(function (s) {
        var msg = s.action_message || ("I'll take " + [s.date, s.window].filter(Boolean).join(", ") + (s.slot ? " (slot " + s.slot + ")" : ""));
        return '<button type="button" class="slot" data-send="' + esc(msg) + '"><span class="sd">' + esc(s.date) + '</span><span class="sw3">' + esc(s.window) + "</span></button>";
      }).join("") + "</div>";
    },
    show_appointment_confirmation: function (a) {
      var nums = arr(a.confirmation_numbers, 4).join(", ");
      return '<div class="receipt appt"><span class="okc" aria-hidden="true">' + ICON.cal + '</span><div class="rt">Appointment confirmed</div>' +
        '<div class="ramt sm">' + esc(a.date) + "</div>" + '<div class="tear" aria-hidden="true"></div>' +
        row("Arrival window", a.window) + row("Service", a.service_type) + row("Address", a.address) + row("Confirmation", nums) + "</div>";
    },
    show_emergency_card: function (a) {
      /* the agent leaves emergency_number empty unless the escalation line is down: fall back to the last phone number in the text */
      var found = String(a.safety_text || "").match(/\d{3}-\d{3}-\d{4}/g);
      a.emergency_number = a.emergency_number || (found ? found[found.length - 1] : "");
      var tel = String(a.emergency_number || "").replace(/[^0-9+]/g, "");
      return '<div class="emerg" role="alert"><div class="eh"><span aria-hidden="true">' + ICON.alert + "</span> Gas emergency</div>" +
        '<p class="et">' + esc(a.safety_text) + "</p>" +
        (tel ? '<a class="ecall" href="tel:' + esc(tel) + '">Call ' + esc(a.emergency_number) + "</a>" : "") + "</div>";
    },
    show_callback_form: function (a) {
      var after = a.reason !== "degraded";
      return head("Request a callback", after ? "We are closed right now" : "We will call you back", ICON.phone) +
        '<form class="cbf" novalidate data-lock="1"><label class="cbl" for="cbn">' + esc(a.prompt || "What number should we call you back on?") + "</label>" +
        '<div class="cbr"><input id="cbn" class="cbi" name="cbn" type="tel" inputmode="tel" autocomplete="tel" placeholder="(555) 123-4567" aria-describedby="cbe" />' +
        '<button type="submit" class="btn">Request callback</button></div>' +
        '<p id="cbe" class="cbe" role="alert" aria-live="polite"></p></form>';
    },
    show_csat_card: function (a) {
      return head(a.prompt || "How did we do?", "Tap a star", "") + '<div class="stars" data-lock="1">' + stars() + "</div>";
    },
    /* Rev 23 chat-screen cards. Same payload rules as every card: all strings are escaped, missing fields are tolerated. */
    show_options_card: function (a) {
      var os = arr(a.options, 4), disp = a.display_text;
      var ttl = a.title && !dupOf(a.title, disp) ? a.title : "Your options";
      var hint = a.prompt && !dupOf(a.prompt, disp) ? a.prompt : "Tap one to continue";
      /* a "no card fee" note under the rows repeats the badge or benefit of a row: drawn once, on the row */
      var feeOnRow = os.some(function (o) { return o && /no (card )?(convenience )?fee/i.test((o.badge || "") + " " + (o.benefit || "")); });
      var fn = a.footnote && !dupOf(a.footnote, disp) && !(disp !== undefined && feeOnRow && /no (card )?(convenience )?fee/i.test(a.footnote)) ? a.footnote : "";
      if (!os.length) { return head(ttl, "", ICON.flame) + empty(); }
      return head(ttl, hint, ICON.flame) +
        '<div class="optl" role="group" aria-label="' + esc(ttl) + '" data-lock="1">' + os.map(function (o) {
          var msg = o.action_message || o.label || "";
          return '<button type="button" class="opt' + (o.highlight ? " hl" : "") + '" data-send="' + esc(msg) + '">' +
            '<span class="ico" aria-hidden="true">' + oicon(o.icon) + "</span>" +
            '<span class="ot"><span class="ol">' + esc(o.label) + "</span>" + (o.benefit ? '<span class="ob">' + esc(o.benefit) + "</span>" : "") + "</span>" +
            (o.badge ? '<span class="bd">' + esc(o.badge) + "</span>" : "") + '<span class="go" aria-hidden="true">&#8250;</span></button>';
        }).join("") + "</div>" + (fn ? '<p class="fn">' + esc(fn) + "</p>" : "");
    },
    show_compare_table_card: function (a) {
      var cols = arr(a.columns, 4), rows = arr(a.rows, 6);
      if (!cols.length || !rows.length) { return head(ttlOf(a, "Compare"), "", ICON.chart) + empty(); }
      var hc = typeof a.highlight_column === "number" && a.highlight_column >= 0 && a.highlight_column < cols.length ? Math.floor(a.highlight_column) : -1;
      var th = cols.map(function (c, i) {
        return '<th scope="col"' + (i === hc ? ' class="hl"' : "") + ">" + (i === hc && a.highlight_label ? '<span class="hb">' + esc(a.highlight_label) + "</span>" : "") + esc(c) + "</th>";
      }).join("");
      var body = rows.map(function (r) {
        var cells = arr(r.cells, 4);
        return '<tr><th scope="row">' + esc(r.label) + (r.note ? '<span class="nt">' + esc(r.note) + "</span>" : "") + "</th>" +
          cols.map(function (_, i) { return "<td" + (i === hc ? ' class="hl"' : "") + ">" + esc(cells[i] === undefined || cells[i] === null ? "" : cells[i]) + "</td>"; }).join("") + "</tr>";
      }).join("");
      return head(ttlOf(a, "Compare"), "", ICON.chart) +
        '<div class="cmpw" role="region" tabindex="0" aria-label="' + esc(ttlOf(a, "Comparison")) + '"><table class="cmp"><caption class="sr">' + esc(ttlOf(a, "Comparison")) + "</caption>" +
        '<thead><tr><th scope="col"><span class="sr">Option</span></th>' + th + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
        (a.caption ? '<p class="fn">' + esc(a.caption) + "</p>" : "") +
        (a.action_label && a.action_message ? '<div class="cact">' + actionBtn(a.action_label, a.action_message, "") + "</div>" : "");
    },
    show_checklist_card: function (a) {
      var its = arr(a.items, 6);
      if (!its.length) { return head(ttlOf(a, "Good to know"), "", ICON.check) + empty(); }
      var steps = a.style === "steps", tag = steps ? "ol" : "ul";
      return head(ttlOf(a, "Good to know"), "", ICON.check) + "<" + tag + ' class="cl">' + its.map(function (it, i) {
        return "<li>" + (steps ? '<span class="dot" aria-hidden="true">' + (i + 1) + "</span>" : '<span class="ico" aria-hidden="true">' + oicon(it.icon) + "</span>") +
          '<span class="ct">' + esc(it.text) + (it.detail ? '<span class="cd">' + esc(it.detail) + "</span>" : "") + "</span></li>";
      }).join("") + "</" + tag + ">" + (a.footnote ? '<p class="fn">' + esc(a.footnote) + "</p>" : "") +
        (a.action_label && a.action_message ? '<div class="cact">' + actionBtn(a.action_label, a.action_message, "") + "</div>" : "");
    }
  };
  var WGL_TOOLS = Object.keys(CARDS);

  var CARD_CSS =
    ":host{display:block;width:100%;font-family:inherit;color:#002a5c;--navy:#002a5c;--blue:#0072bc;--gold:#ffc72c;--pale:#f3fafe;--line:#d3e3f1;--ink2:#495057}" +
    "*{box-sizing:border-box}" +
    ".card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:0 6px 18px rgba(0,42,92,.10);animation:rise .38s cubic-bezier(.2,.8,.2,1) both;overflow:hidden}" +
    ".card.bare{padding:0;background:transparent;border:0;box-shadow:none}" +
    "@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}" +
    "@keyframes grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}" +
    "@keyframes draw{from{stroke-dashoffset:600}to{stroke-dashoffset:0}}" +
    ".card-head{display:flex;gap:10px;align-items:center;margin-bottom:10px}" +
    ".ico{flex:none;width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:linear-gradient(135deg,#002a5c,#0072bc);color:#fff}" +
    ".ico svg{display:block}" +
    ".title{font-weight:700;font-size:15px;line-height:1.25}" +
    ".sub{font-size:12px;color:var(--ink2);margin-top:1px}" +
    ".chart{width:100%;height:auto;display:block;overflow:visible}" +
    ".bar{transform-box:fill-box;transform-origin:50% 100%;animation:grow .55s cubic-bezier(.2,.8,.2,1) both}" +
    ".line{stroke-dasharray:600;animation:draw 1s .3s ease-out both}" +
    ".seg{animation:fade .6s ease both}@keyframes fade{from{opacity:0}to{opacity:1}}" +
    ".ax{font-size:9.5px;fill:#495057;font-family:inherit}.ax.cur{fill:#002a5c;font-weight:700}" +
    ".val{font-size:10.5px;fill:#002a5c;font-weight:700;font-family:inherit}" +
    ".dl{font-size:10px;fill:#495057}.dv{font-size:16px;font-weight:800;fill:#002a5c}" +
    ".stats{display:flex;gap:10px;margin-top:8px}.stats>div{flex:1;background:var(--pale);border:1px solid #c0ecff;border-radius:10px;padding:6px 10px;font-size:13px}" +
    ".stats .k{display:block;font-size:11px;color:var(--ink2)}" +
    ".note{margin:10px 0 0;padding:8px 10px;background:#fff8e1;border-left:4px solid var(--gold);border-radius:8px;font-size:13px;line-height:1.35}" +
    ".legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--ink2);margin-top:6px}.legend span{display:inline-flex;align-items:center;gap:6px}" +
    ".sw{display:inline-block;width:12px;height:12px;border-radius:3px;flex:none}.sw.bar{background:var(--blue);animation:none}.sw.ln{height:3px;width:16px;background:#b45309;border-radius:2px}" +
    ".donut-row{display:flex;gap:12px;align-items:center}.donut-row .chart{width:128px;flex:none}" +
    ".lg{list-style:none;margin:0;padding:0;flex:1;min-width:0;font-size:13px}.lg li{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid #e9f0f6}.lg li:last-child{border:0}" +
    ".lg .lb{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lg b{font-variant-numeric:tabular-nums}" +
    ".car{position:relative}.track{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none;padding:2px 2px 6px;border-radius:12px;outline-offset:2px}" +
    ".track::-webkit-scrollbar{display:none}.slide{flex:0 0 88%;scroll-snap-align:center}" +
    ".nav{position:absolute;bottom:-5px;width:28px;height:28px;border-radius:50%;border:1px solid var(--line);background:#fff;color:var(--navy);font-size:20px;line-height:1;cursor:pointer;box-shadow:0 2px 8px rgba(0,42,92,.2);display:grid;place-items:center;padding:0 0 3px}" +
    ".nav.prev{left:calc(50% - 64px)}.nav.next{right:calc(50% - 64px)}.nav:hover{background:var(--pale)}" +
    ".dots{display:flex;justify-content:center;align-items:center;gap:6px;margin-top:4px;height:28px}.dots i{width:7px;height:7px;border-radius:50%;background:#b6c8d9;transition:all .2s}.dots i.on{background:var(--navy);width:18px;border-radius:4px}" +
    ".ins{height:100%;background:#fff;border:1px solid var(--line);border-left:5px solid var(--blue);border-radius:14px;padding:12px;box-shadow:0 6px 16px rgba(0,42,92,.10);display:flex;flex-direction:column;gap:6px}" +
    ".ins .badge{align-self:flex-start;font-size:11px;font-weight:700;border-radius:999px;padding:2px 9px 2px 5px;background:#e3f1fb;color:#00406f;display:inline-flex;align-items:center;gap:5px}" +
    ".ins .badge b{display:inline-grid;place-items:center;width:15px;height:15px;border-radius:50%;background:#00406f;color:#fff;font-size:10px}" +
    ".ins.sev-attention{border-left-color:#d98a00}.ins.sev-attention .badge{background:#fff0cc;color:#6b4500}.ins.sev-attention .badge b{background:#6b4500}" +
    ".ins.sev-urgent{border-left-color:#c8261b}.ins.sev-urgent .badge{background:#fde3e1;color:#8a1810}.ins.sev-urgent .badge b{background:#8a1810}" +
    ".ins.sev-saving{border-left-color:#2f8a4d}.ins.sev-saving .badge{background:#def4e5;color:#1d5b33}.ins.sev-saving .badge b{background:#1d5b33}" +
    ".sum{margin:0;font-size:13px;line-height:1.4;color:#33414f;flex:1}" +
    ".btn{font:inherit;font-weight:700;font-size:13px;padding:8px 14px;border-radius:10px;border:2px solid var(--navy);background:var(--navy);color:#fff;cursor:pointer;align-self:flex-start;transition:transform .12s,background .15s,box-shadow .15s}" +
    ".btn:hover{background:#00408a;box-shadow:0 3px 0 var(--gold)}.btn:active{transform:translateY(1px)}" +
    "button:focus-visible,a:focus-visible,.track:focus-visible{outline:3px solid var(--gold);box-shadow:0 0 0 5px var(--navy)}" +
    ".stack{display:flex;flex-direction:column;gap:10px}" +
    ".rb{border:1px solid var(--line);background:var(--pale);border-radius:12px;padding:10px 12px;display:flex;flex-direction:column;gap:5px}" +
    ".rb-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.rb-name{font-weight:700;font-size:14px}" +
    ".rb-amt{flex:none;font-weight:800;font-size:15px;background:var(--gold);color:var(--navy);border-radius:8px;padding:2px 8px}" +
    ".rb-dl{font-size:12px;color:#7a3e00;font-weight:600}.rb-note{font-size:13px;color:#33414f}" +
    ".steps{list-style:none;margin:0;padding:0}.step{display:flex;align-items:center;gap:10px;position:relative;padding:6px 0;font-size:14px}" +
    ".step:not(:last-child)::after{content:'';position:absolute;left:13px;top:32px;bottom:-6px;width:2px;background:#c5d6e6}.step.done:not(:last-child)::after{background:var(--blue)}" +
    ".dot{flex:none;width:28px;height:28px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;border:2px solid #9fb6cc;background:#fff;color:#495057;z-index:1}" +
    ".step.done .dot{background:var(--blue);border-color:var(--blue);color:#fff}.step.done .dot svg{width:15px;height:15px}" +
    ".step.current .dot{border-color:var(--navy);background:var(--gold);color:var(--navy);animation:pulse 1.8s infinite}" +
    "@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(255,199,44,.7)}70%{box-shadow:0 0 0 9px rgba(255,199,44,0)}100%{box-shadow:0 0 0 0 rgba(255,199,44,0)}}" +
    ".sl{flex:1;font-weight:600}.step.todo .sl{font-weight:400;color:var(--ink2)}.sw2{font-size:11px;color:var(--ink2);font-weight:600}" +
    ".receipt{text-align:center;background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px 16px 12px;box-shadow:0 6px 18px rgba(0,42,92,.10);animation:rise .38s both}" +
    ".okc{display:inline-grid;place-items:center;width:44px;height:44px;border-radius:50%;background:#2f8a4d;color:#fff;margin-bottom:6px;animation:pop .45s .1s cubic-bezier(.3,1.6,.5,1) both}" +
    "@keyframes pop{from{transform:scale(0)}to{transform:scale(1)}}" +
    ".receipt.appt .okc{background:var(--navy)}.rt{font-weight:700;font-size:14px;color:var(--ink2)}.ramt{font-size:30px;font-weight:800;letter-spacing:-.5px;margin:2px 0 8px}.ramt.sm{font-size:20px}.ramt.left{text-align:left;font-size:28px}" +
    ".tear{height:0;border-top:2px dashed #b6c8d9;margin:4px -4px 8px}" +
    ".kv{display:flex;justify-content:space-between;gap:12px;padding:4px 0;font-size:13px;text-align:left}.kv .k{color:var(--ink2)}.kv .v{font-weight:700;text-align:right;overflow-wrap:anywhere}" +
    ".chips{display:flex;flex-wrap:wrap;gap:8px}.chip{font:inherit;font-weight:600;font-size:13.5px;padding:8px 14px;border-radius:999px;border:1.5px solid var(--blue);background:#fff;color:#00589a;cursor:pointer;transition:all .15s;animation:rise .3s both}" +
    ".chip:nth-child(2){animation-delay:60ms}.chip:nth-child(3){animation-delay:120ms}.chip:nth-child(4){animation-delay:180ms}.chip:nth-child(5){animation-delay:240ms}" +
    ".chips.follow{margin-top:10px}" +
    ".wel{display:flex;flex-direction:column;gap:10px;animation:rise .4s cubic-bezier(.2,.8,.2,1) both}" +
    ".langrow{display:inline-flex;align-items:center;align-self:flex-start;gap:2px;background:var(--pale);border:1px solid var(--line);border-radius:999px;padding:3px 4px 3px 10px;font-size:12.5px}" +
    ".globe{font-size:13px;margin-right:4px}.sep{color:#9fb6cc}" +
    ".lang{font:inherit;font-weight:600;font-size:12.5px;color:#00589a;background:transparent;border:0;border-radius:999px;padding:4px 10px;cursor:pointer;transition:background .15s,color .15s}" +
    ".lang:hover{background:#e3f1fb}.lang.on{background:var(--navy);color:#fff}.lang:disabled{cursor:default;opacity:.6}.lang.on:disabled{opacity:1}" +
    ".live{display:contents}.lnote{margin:0;max-width:88%;padding:10px 13px;background:#eef6fc;border:1px solid #c0ecff;border-radius:16px;border-top-left-radius:2px;font-size:14.5px;line-height:1.45;color:var(--navy);animation:rise .3s both}" +
    ".wel.sent .chip{opacity:.55;cursor:default;pointer-events:none}.wel.sent .chip.picked{opacity:1;background:var(--navy);color:#fff;border-color:var(--navy)}" +
    ".chip:hover{background:var(--blue);color:#fff;transform:translateY(-1px);box-shadow:0 3px 0 var(--gold)}" +
    ".slots{display:flex;flex-direction:column;gap:8px}.slot{font:inherit;display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;border-radius:12px;border:1.5px solid var(--line);background:var(--pale);color:var(--navy);cursor:pointer;text-align:left;transition:all .15s}" +
    ".slot:hover{border-color:var(--blue);background:#e3f1fb}.sd{font-weight:700;font-size:14px}.sw3{font-size:13px;color:var(--ink2);font-weight:600}" +
    "[data-lock].done button{opacity:.55;cursor:default;pointer-events:none;box-shadow:none;transform:none}[data-lock].done button.picked{opacity:1;background:var(--navy);color:#fff;border-color:var(--navy)}" +
    ".emerg{background:#8a1810;color:#fff;border-radius:16px;padding:14px;border:3px solid #ffc72c;box-shadow:0 8px 22px rgba(138,24,16,.35)}" +
    ".eh{font-weight:800;font-size:16px;display:flex;align-items:center;gap:8px}.et{margin:8px 0 12px;font-size:14px;line-height:1.45}" +
    ".ecall{display:block;text-align:center;font-weight:800;font-size:17px;background:var(--gold);color:var(--navy);padding:10px;border-radius:10px;text-decoration:none}" +
    ".stars{display:flex;gap:2px;justify-content:center}.star{background:none;border:0;padding:2px;color:#b6c8d9;cursor:pointer;transition:transform .12s,color .12s}.star:hover,.star:focus-visible{color:#d99a00;transform:scale(1.15)}" +
    "[data-lock].done .star.picked{color:#d99a00;opacity:1;background:none}" +
    ".empty{margin:0;font-size:13px;color:var(--ink2)}" +
    ".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}" +
    ".optl{display:flex;flex-direction:column;gap:8px}" +
    ".opt{font:inherit;display:flex;gap:10px;align-items:center;width:100%;text-align:left;padding:10px 12px;border-radius:12px;border:1.5px solid var(--line);background:var(--pale);color:var(--navy);cursor:pointer;transition:all .15s;animation:rise .3s both}" +
    ".opt:nth-child(2){animation-delay:60ms}.opt:nth-child(3){animation-delay:120ms}.opt:nth-child(4){animation-delay:180ms}" +
    ".opt:hover{border-color:var(--blue);background:#e3f1fb;transform:translateY(-1px)}" +
    ".opt.hl{border-color:var(--gold);background:#fff8e1;box-shadow:inset 4px 0 0 var(--gold)}" +
    ".opt .ot{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}.opt .ol{font-weight:700;font-size:14px;line-height:1.25}.opt .ob{font-size:12.5px;color:var(--ink2);line-height:1.3}" +
    ".opt .bd{flex:none;align-self:flex-start;font-size:11px;font-weight:700;background:var(--gold);color:var(--navy);border-radius:999px;padding:2px 8px}" +
    ".opt .go{flex:none;color:var(--blue);font-size:22px;line-height:1}" +
    ".opt .ico,.cl .ico{width:32px;height:32px;border-radius:10px}" +
    ".fn{margin:10px 0 0;font-size:12px;color:var(--ink2);line-height:1.4}.cact{margin-top:10px}" +
    ".cbf{display:flex;flex-direction:column;gap:6px}.cbl{font-size:13.5px;color:var(--ink2);line-height:1.35}.cbr{display:flex;gap:8px;flex-wrap:wrap}" +
    ".cbi{flex:1;min-width:150px;font:inherit;font-size:15px;padding:9px 12px;border:1.5px solid var(--line);border-radius:10px;color:var(--navy);background:#fff}.cbi:focus{outline:2px solid var(--blue);outline-offset:1px;border-color:var(--blue)}" +
    ".cbe{margin:0;min-height:16px;font-size:12.5px;color:#9b1c1c}.cbf.done .cbi,.cbf.done .btn{opacity:.55;pointer-events:none}.cbf.done .cbe{color:#1b6e3c}" +
    ".cmpw{overflow-x:auto;border:1px solid var(--line);border-radius:12px;outline-offset:2px}" +
    ".cmp{width:100%;border-collapse:collapse;font-size:13px}" +
    ".cmp th,.cmp td{padding:8px 9px;text-align:center;border-bottom:1px solid #e9f0f6;font-variant-numeric:tabular-nums}" +
    ".cmp tbody tr:last-child th,.cmp tbody tr:last-child td{border-bottom:0}" +
    ".cmp thead th{background:var(--navy);color:#fff;font-size:12px;font-weight:700;vertical-align:bottom}" +
    ".cmp tbody th{text-align:left;font-weight:700;background:var(--pale);min-width:84px}" +
    ".cmp .nt{display:block;font-weight:400;font-size:11.5px;color:var(--ink2);margin-top:1px}" +
    ".cmp td.hl{background:#fff8e1;font-weight:700}.cmp thead th.hl{background:var(--gold);color:var(--navy)}" +
    ".cmp .hb{display:block;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;margin-bottom:1px}" +
    ".cl{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px}.cl li{display:flex;gap:10px;align-items:flex-start}" +
    ".cl .ct{flex:1;min-width:0;font-size:14px;line-height:1.35;font-weight:600;padding-top:5px}.cl .cd{display:block;font-weight:400;font-size:12.5px;color:var(--ink2);margin-top:1px}" +
    ".cl .dot{background:var(--navy);border-color:var(--navy);color:#fff}" +
    ".rsn{list-style:none;margin:10px 0 0;padding:0;display:flex;flex-direction:column;gap:6px}.rsn li{display:flex;align-items:center;gap:9px;font-size:13.5px;line-height:1.3}" +
    ".rsn .rico{flex:none;width:26px;height:26px;border-radius:8px;display:grid;place-items:center;background:var(--pale);border:1px solid #c0ecff;color:var(--navy)}.rsn .rico svg{width:16px;height:16px}" +
    ".rsn .rl{flex:1;min-width:0}.rsn .ra{flex:none;font-variant-numeric:tabular-nums;color:#7a3e00}.rsn .ra.dn{color:#1d5b33}" +
    ".wstats{flex-wrap:wrap}.wstats>div{flex:1 1 40%;min-width:0}" +
    "@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.line{stroke-dasharray:none}}";

  /* Bare cards (no outer frame): they draw their own frame */
  var BARE = { show_insights_carousel: 1, show_payment_receipt_card: 1, show_appointment_confirmation: 1, show_emergency_card: 1, show_quick_actions: 1, show_welcome_chips: 1 };

  function buildCardHTML(tool, args) {
    var fn = CARDS[tool];
    if (!fn) { return ""; }
    var inner;
    try { inner = fn(args && typeof args === "object" ? args : {}); }
    catch (e) { inner = empty(); }
    /* Rev 29b: a card never draws display_text itself; it is the bot bubble above the card (typed and voice alike) */
    return "<style>" + CARD_CSS + '</style><div class="card' + (BARE[tool] ? " bare" : "") + '">' + inner + "</div>";
  }

  /* Send a customer message through the chat. Mirrors what the SDK's own chips do. */
  function sendUserMessage(text, tool, source) {
    var ev = new CustomEvent("wgl-send", { detail: { text: text, tool: tool }, bubbles: true, composed: true, cancelable: true });
    if (!source.dispatchEvent(ev)) { return; }   /* host page handled it (preview page) */
    var el = chatEl();
    if (el && typeof el.renderCustomText === "function" && el.presenter && typeof el.presenter.sendQuery === "function") {
      el.renderCustomText(text, false);
      el.presenter.sendQuery(text);
      el.dispatchEvent(new CustomEvent("chat-messenger-user-input-entered", { detail: {}, bubbles: true, composed: true }));
    } else if (window.console) {
      console.warn("[wgl] chat-messenger not ready, could not send:", text);
    }
  }

  /* One class per element name: customElements.define needs a distinct constructor for each tag.
     "wgl-card" takes {tool, args} (preview page, console); "cx-<tool-name>" (what the platform sends: a
     custom_template rich element named after the tool, payload = the card fields) derives the tool from its tag. */
  var welcomeCards = [];   /* live welcome cards: locked as soon as the visitor sends anything */
  function lockWelcomeCards() { welcomeCards.forEach(function (c) { c._lockWelcome(); }); }

  function defineCard(tag) {
    if (!window.customElements || customElements.get(tag)) { return; }
    var WglCard = function () { return Reflect.construct(HTMLElement, [], WglCard); };
    WglCard.prototype = Object.create(HTMLElement.prototype, { constructor: { value: WglCard } });
    Object.setPrototypeOf(WglCard, HTMLElement);
    Object.defineProperty(WglCard.prototype, "dfPayload", {
      get: function () { return this._p; },
      set: function (v) { this._p = v; if (this._root) { this._render(); } }
    });
    WglCard.prototype.connectedCallback = function () {
      if (this._root) { return; }
      try { this._root = this.attachShadow({ mode: "open" }); } catch (err) { console.warn("[wgl] card shadow root failed:", err); return; }
      this._render();
      var self = this;
      this._root.addEventListener("click", function (ev) { self._click(ev); });
      this._root.addEventListener("submit", function (ev) { self._submit(ev); });
      var track = this._root.querySelector(".track");
      if (track) {
        track.addEventListener("scroll", function () { self._dots(track); }, { passive: true });
        track.addEventListener("keydown", function (e) {
          if (e.key === "ArrowRight") { self._go(track, 1); e.preventDefault(); }
          if (e.key === "ArrowLeft") { self._go(track, -1); e.preventDefault(); }
        });
      }
    };
    WglCard.prototype._render = function () {
      var p = this._p || {};
      var tool = p.tool || TAG_TOOL[this.localName];
      var args = p.tool ? (p.args || p.data || {}) : p;
      this._tool = tool;
      try {
        this._root.innerHTML = buildCardHTML(tool, args);
        if (tool === "show_welcome_chips" && welcomeCards.indexOf(this) < 0) { welcomeCards.push(this); }
      } catch (err) { console.warn("[wgl] card render failed for", tool, err); }
    };
    WglCard.prototype._go = function (track, dir) {
      var s = track.querySelector(".slide");
      var w = s ? s.getBoundingClientRect().width + 10 : 280;
      var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      track.scrollBy({ left: dir * w, behavior: reduce ? "auto" : "smooth" });
    };
    WglCard.prototype._dots = function (track) {
      var s = track.querySelector(".slide");
      var w = s ? s.getBoundingClientRect().width + 10 : 280;
      var idx = Math.round(track.scrollLeft / w);
      var dots = this._root.querySelectorAll(".dots i");
      for (var i = 0; i < dots.length; i++) { dots[i].className = i === idx ? "on" : ""; }
    };
    WglCard.prototype._click = function (ev) {
      var t = ev.target;
      while (t && t !== this._root && !(t.getAttribute && (t.hasAttribute("data-send") || t.hasAttribute("data-nav") || t.hasAttribute("data-lang")))) { t = t.parentNode; }
      if (!t || t === this._root) { return; }
      if (t.hasAttribute("data-lang")) { this._lang(t.getAttribute("data-lang")); return; }
      if (t.hasAttribute("data-nav")) { this._go(this._root.querySelector(".track"), Number(t.getAttribute("data-nav"))); return; }
      var group = t.closest ? t.closest("[data-lock]") : null;
      if (group) {
        if (group.classList.contains("done")) { return; }
        group.classList.add("done");
        t.classList.add("picked");
      }
      if (this._tool === "show_welcome_chips") {
        /* the first agent turn: the chip text goes out with page_context and the chosen ui_language as widget variables */
        var chosen = this._p.lang === "es" ? "es" : "en";
        var pg = this._p.page || "home";
        this._p.picked = t.getAttribute("data-send");
        this._lockWelcome();
        setVars({ page_context: window.wglPageContext || pg, ui_language: chosen });
      }
      sendUserMessage(t.getAttribute("data-send"), this._tool, this);
    };
    WglCard.prototype._submit = function (ev) {
      ev.preventDefault();
      var form = ev.target;
      if (!form || !form.classList || !form.classList.contains("cbf") || form.classList.contains("done")) { return; }
      var input = form.querySelector(".cbi"), note = form.querySelector(".cbe");
      var digits = String(input.value || "").replace(/\D/g, "");
      if (digits.length === 11 && digits.charAt(0) === "1") { digits = digits.slice(1); }
      if (digits.length !== 10 || /^[01]/.test(digits)) {
        if (note) { note.textContent = "Please enter a 10-digit phone number."; }
        input.setAttribute("aria-invalid", "true");
        input.focus();
        return;
      }
      input.removeAttribute("aria-invalid");
      var shown = digits.slice(0, 3) + "-" + digits.slice(3, 6) + "-" + digits.slice(6);
      form.classList.add("done");
      if (note) { note.textContent = "Thanks, sending your number."; }
      sendUserMessage("Please call me back at " + shown, this._tool, this);
    };
    WglCard.prototype._lang = function (code) {
      var p = this._p;
      if (!p || this._tool !== "show_welcome_chips" || p.sent || (code !== "en" && code !== "es")) { return; }
      p.lang = code;
      writeLang(code, p.page || pageKey());
      setVars({ ui_language: code });
      this._render();
      var b = this._root.querySelector('[data-lang="' + code + '"]');
      if (b) { b.focus(); }
    };
    WglCard.prototype._lockWelcome = function () {
      if (this._tool !== "show_welcome_chips" || !this._p || this._p.sent) { return; }
      this._p.sent = true;
      this._render();
    };
    customElements.define(tag, WglCard);
  }
  var TAG_TOOL = {};
  WGL_TOOLS.forEach(function (name) { TAG_TOOL["cx-" + name.replace(/_/g, "-")] = name; });
  defineCard("wgl-card");
  WGL_TOOLS.forEach(function (name) { defineCard("cx-" + name.replace(/_/g, "-")); });

  /* ---- SDK wiring: client-side function per card tool + response fallback ---- */
  var recent = {};
  function renderTool(name, args) {
    var el = chatEl();
    if (!el || typeof el.renderCustomCard !== "function" || !CARDS[name]) { return false; }
    var key = name + "|" + JSON.stringify(args || {});
    var now = Date.now();
    if (recent[key] && now - recent[key] < 10000) { return true; }
    recent[key] = now;
    el.renderCustomCard([{ type: "custom_template", name: "wgl-card", id: "wgl-" + now, payload: { tool: name, args: args || {} } }]);
    return true;
  }
  var registered = false;
  function registerTools() {
    if (registered || PREVIEW) { return registered; }
    var el = chatEl();
    if (!el || !el.presenter || typeof el.presenter.registerClientSideFunctionForAllTools !== "function") { return false; }
    WGL_TOOLS.forEach(function (name) {
      el.presenter.registerClientSideFunctionForAllTools(name, function (args) {
        renderTool(name, args);
        return { rendered: true };
      });
    });
    registered = true;
    return true;
  }
  function lastSegment(p) { var s = String(p || "").split("/"); return s[s.length - 1]; }
  /* Rich element types the SDK renders itself. Anything else inside a customCard makes the SDK throw while it
     draws the message (TypeError in its card renderer), and then the WHOLE reply, text included, never appears.
     That is what happens when the platform delivers a widget tool in its built-in shape {type: "quick_actions",
     prompt, options} (a web deployment pinned to an old app version does this) instead of a custom_template. */
  var SDK_TYPES = { info: 1, description: 1, image: 1, list: 1, button: 1, accordion: 1, chips: 1, html: 1, match_citations: 1,
    custom_template: 1, video: 1, files: 1, divider: 1, table: 1, text: 1, customCard: 1 };
  function cardTagFor(type) {
    var tool = "show_" + String(type || "").replace(/^show_/, "");
    return CARDS[tool] ? "cx-" + tool.replace(/_/g, "-") : "";
  }
  /* Runs before the SDK draws the reply (the response-received event fires first, and the messages array is the one
     the SDK renders): turn built-in-typed elements into our custom elements, drop what nothing can render, keep text. */
  function normalizeMessages(msgs) {
    if (!Array.isArray(msgs)) { return; }
    for (var i = msgs.length - 1; i >= 0; i--) {
      var m = msgs[i];
      if (!m || typeof m !== "object") { msgs.splice(i, 1); continue; }
      var els = m.type === "customCard" && Array.isArray(m.richElements) ? m.richElements : null;
      var list = els || [m];
      var out = [];
      list.forEach(function (el) {
        if (!el || typeof el !== "object") { return; }
        if (!el.type && typeof el.name === "string" && /^cx-/.test(el.name)) { el.type = "custom_template"; }   /* flat {name, payload} from a bidi payload */
        if (SDK_TYPES[el.type] && !(el.type === "custom_template" && !(window.customElements && customElements.get(el.name)))) { out.push(el); return; }
        var tag = el.type === "custom_template" ? "" : cardTagFor(el.type);
        if (tag) {
          var fields = {};
          Object.keys(el).forEach(function (k) { if (k !== "type" && k !== "id" && k !== "name") { fields[k] = el[k]; } });
          if (el.type === "custom_template" && el.payload) { fields = el.payload; }
          out.push({ type: "custom_template", name: tag, id: el.id || "wgl-" + Date.now() + "-" + i, payload: fields });
          console.warn("[wgl] converted a built-in '" + el.type + "' element into <" + tag + ">");
        } else {
          console.warn("[wgl] dropped an element the chat cannot render:", el.type, el.name || "");
        }
      });
      if (!els) {
        if (out.length === 1 && out[0] !== m) { msgs[i] = out[0]; } else if (!out.length) { msgs.splice(i, 1); }
      } else {
        var plain = out.filter(function (x) { return x.type !== "custom_template"; });
        var cards = out.filter(function (x) { return x.type === "custom_template"; });
        var repl = [];
        if (plain.length) { m.richElements = plain; repl.push(m); }
        cards.forEach(function (c) { repl.push(c); });
        Array.prototype.splice.apply(msgs, [i, 1].concat(repl));
      }
    }
  }
  /* ---- Rev 27/28/29b speech: the short bubble above every card ------------------------------------------------------
     The agent's text part of a card reply is the full spoken explanation (the voice reads it aloud); the card payload carries
     display_text, the one or two short sentences shown on screen. Typed chat: the long text message of the reply is replaced by
     display_text (a normal bot bubble above the card). Voice (bidi) mode: the widget plays the backend audio and shows the transcript
     as a bot bubble; the page hides that long bubble once the card with display_text arrives and puts display_text in front of the
     card as an ordinary bot bubble. A card never draws display_text itself. Audio is never touched. Without display_text, or if
     anything throws or the bubble is not found, the long text stays visible. */
  function cardFieldsOf(el) {
    if (!el || typeof el !== "object") { return null; }
    var p = el.payload && typeof el.payload === "object" ? el.payload : el;
    if (p && p.args && typeof p.args === "object" && typeof p.display_text !== "string") { p = p.args; }
    if (p && p.payload && typeof p.payload === "object" && typeof p.display_text !== "string" && typeof p.payload.display_text === "string") { p = p.payload; }
    return p && typeof p === "object" ? p : null;
  }
  function displayTextOf(el) {
    var p = cardFieldsOf(el);
    var d = p && p.display_text;
    return typeof d === "string" ? d.replace(/^\s+|\s+$/g, "") : "";
  }
  /* The display_text of a reply: from a flat card message or from the rich elements of a customCard message. */
  function leadOf(msgs) {
    if (!Array.isArray(msgs)) { return ""; }
    for (var i = 0; i < msgs.length; i++) {
      var m = msgs[i];
      if (!m || typeof m !== "object" || m.type === "text") { continue; }
      var d = displayTextOf(m);
      if (!d && Array.isArray(m.richElements)) {
        for (var j = 0; j < m.richElements.length && !d; j++) { d = displayTextOf(m.richElements[j]); }
      }
      if (d) { return d; }
    }
    return "";
  }
  function isCardMessage(m) { return !!m && typeof m === "object" && m.type !== "text" && m.type !== "chips"; }
  /* Typed path: runs on the messages array of chat-messenger-response-received, after normalizeMessages. */
  function applyDisplayText(msgs) {
    var lead = leadOf(msgs);
    if (!lead) { return false; }
    var first = -1, firstCard = -1;
    for (var i = 0; i < msgs.length; i++) {
      var m = msgs[i];
      if (first < 0 && m && m.type === "text") { first = i; }
      if (firstCard < 0 && isCardMessage(m)) { firstCard = i; }
    }
    if (first >= 0 && msgs[first]._wglLead) { return false; }   /* already applied */
    for (var k = msgs.length - 1; k >= 0; k--) {
      if (msgs[k] && msgs[k].type === "text" && k !== first) {
        msgs.splice(k, 1);
        if (k < first) { first--; }
        if (k < firstCard) { firstCard--; }
      }
    }
    if (first >= 0) {
      msgs[first].text = lead;
      msgs[first]._wglLead = true;
    } else {
      msgs.splice(Math.max(firstCard, 0), 0, { type: "text", text: lead, _wglLead: true });
    }
    return true;
  }

  var voice = { on: false, texts: [], hidden: {}, lead: "", registered: false };
  var voiceConsumer = {
    consumerId: "wgl-speech-pilot",
    clearMessages: function () { voice.texts = []; voice.hidden = {}; voice.lead = ""; },
    addMessages: function (utts) { try { voiceAdd(utts); } catch (err) { console.warn("[wgl] speech pilot add failed (text stays visible):", err); } return Promise.resolve(); },
    updateMessages: function (utts) { try { voiceUpdate(utts); } catch (err) { /* text stays visible */ } return Promise.resolve(); },
    removeMessage: function () {},
    removeLastMessage: function () {}
  };
  function utterEl(id) {
    var found = null;
    function walk(rootNode) {
      var all = rootNode.querySelectorAll("*");
      for (var i = 0; i < all.length && !found; i++) {
        var e = all[i];
        if (e.localName === "chat-messenger-utterance" && ((e.utterance && e.utterance.utteranceId) === id || e.utteranceId === id)) { found = e; return; }
        if (e.shadowRoot) { walk(e.shadowRoot); }
      }
    }
    walk(doc);   /* the utterances live in the shadow roots below the chat-messenger element and its container */
    return found;
  }
  function hideUtterance(id, tries) {
    if (!id) { return; }
    voice.hidden[id] = true;
    var e = utterEl(id);
    if (e) { e.style.display = "none"; e.setAttribute("data-wgl-hidden", "speech"); return; }
    if ((tries || 0) < 30) { setTimeout(function () { hideUtterance(id, (tries || 0) + 1); }, 60); }
  }
  /* A bidi payload can arrive in a shape the SDK cannot draw; convert the known card shapes in place, as the typed path does. */
  function fixRich(msgs) {
    msgs.forEach(function (m) {
      if (!m || m.type !== "customCard" || !Array.isArray(m.richElements)) { return; }
      m.richElements.forEach(function (el) {
        if (!el || typeof el !== "object") { return; }
        if (!el.type && typeof el.name === "string" && /^cx-/.test(el.name)) { el.type = "custom_template"; return; }
        if (!SDK_TYPES[el.type]) {
          var tag = cardTagFor(el.type);
          if (!tag) { return; }
          var fields = {};
          Object.keys(el).forEach(function (k) { if (k !== "type" && k !== "id" && k !== "name") { fields[k] = el[k]; } });
          Object.keys(el).forEach(function (k) { delete el[k]; });
          el.type = "custom_template"; el.name = tag; el.id = "wgl-" + Date.now(); el.payload = fields;
        }
      });
    });
  }
  function textOf(msgs) {
    return msgs.filter(function (m) { return m && m.type === "text" && typeof m.text === "string"; }).map(function (m) { return m.text; }).join(" ").replace(/^\s+|\s+$/g, "");
  }
  /* The long spoken text is longer than the lead; the idle prompt of the voice mode and the lead itself are never hidden. */
  function isLongText(t, lead) {
    return !!t && t !== lead && t.length > lead.length && !/still here|take your time/i.test(t);
  }
  /* Rev 29b: the short display_text is a normal bot bubble ABOVE the card, typed and voice alike. Typed: applyDisplayText already put it
     in the reply. Voice (bidi): the card arrives as its own utterance after the long spoken text, so the page inserts a text message
     with display_text in front of the card in that same utterance (the SDK draws it as an ordinary bot bubble) and hides the long
     text bubble. Nothing is ever drawn inside the card. If the bubble cannot be found on screen shortly afterwards, the long text
     is shown again (the fallback of the pilot: the customer always sees an answer). */
  /* Rev 29c: compared as normalized plain text. The SDK draws markdown (**bold** becomes <strong>, "\n" becomes <br> with no whitespace text
     node between the lines, links lose their syntax), so the raw display_text never matched the DOM text. Everything is reduced to lowercase
     letters and digits only before comparing, and only the first 30 such characters of the lead are looked for. */
  function plainText(t) {
    var s = String(t || "");
    s = s.replace(/&nbsp;/gi, " ").replace(/&amp;/gi, "&").replace(/&lt;/gi, "<").replace(/&gt;/gi, ">").replace(/&quot;/gi, '"').replace(/&#0*39;/g, "'");
    s = s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/(\*\*|__|`)/g, "");
    return s.toLowerCase().replace(/[^a-z0-9]+/g, "");
  }
  function leadKey(lead) { return plainText(lead).slice(0, 30); }
  function deepText(rootNode) {
    var out = "";
    (function walk(r) {
      var kids = r.childNodes || [];
      for (var i = 0; i < kids.length; i++) {
        var k = kids[i];
        if (k.nodeType === 3) { out += k.nodeValue; }
        else if (k.nodeType === 1) { if (k.shadowRoot) { walk(k.shadowRoot); } walk(k); }
      }
    })(rootNode);
    return out.replace(/\s+/g, " ");
  }
  function showUtterance(id) {
    delete voice.hidden[id];
    var e = utterEl(id);
    if (e) { e.style.display = ""; e.removeAttribute("data-wgl-hidden"); }
  }
  var leadWarned = false;
  function verifyLead(cardId, lead, hiddenIds, tries) {
    var e = utterEl(cardId);
    var want = leadKey(lead);
    if (!want) { return; }
    if (e && plainText(deepText(e.shadowRoot || e)).indexOf(want) > -1) { return; }
    if ((tries || 0) < 12) { setTimeout(function () { verifyLead(cardId, lead, hiddenIds, (tries || 0) + 1); }, 150); return; }
    if (!leadWarned) { leadWarned = true; try { console.warn("[wgl] the lead bubble did not appear; the long text is shown again"); } catch (err) { /* ignore */ } }
    hiddenIds.forEach(showUtterance);
  }  function insertLeadBubble(messages, lead) {
    for (var i = 0; i < messages.length; i++) {
      var m = messages[i];
      if (m && m.type === "customCard") { messages.splice(i, 0, { type: "text", text: lead, _wglLead: true }); return true; }
    }
    return false;
  }
  function voiceAdd(utts) {
    if (!Array.isArray(utts)) { return; }
    utts.forEach(function (u) {
      if (!u || !Array.isArray(u.messages)) { return; }
      if (!u.isBot) { voice.texts = []; voice.lead = ""; return; }   /* a new customer turn starts a new reply */
      fixRich(u.messages);
      var lead = leadOf(u.messages);
      if (lead) {
        /* typed sequence: the lead is already on screen as the bot text (a text message of the same utterance, or the text utterance
           just before the card), so nothing is added and nothing is hidden */
        var typed = u.messages.some(function (m) { return m && m.type === "text" && m.text === lead; }) ||
          voice.texts.some(function (t) { return t.text === lead; });
        if (!typed) {
          voice.lead = lead;
          var hiddenIds = [];
          voice.texts.forEach(function (t) { if (isLongText(t.text, lead)) { hideUtterance(t.id, 0); hiddenIds.push(t.id); } });
          if (insertLeadBubble(u.messages, lead) && u.utteranceId) { setTimeout(function () { verifyLead(u.utteranceId, lead, hiddenIds, 0); }, 200); }
          else { hiddenIds.forEach(showUtterance); }   /* no way to draw the lead: the long text stays */
        }
        voice.texts = [];
        return;
      }
      var isText = u.messages.some(function (m) { return m && m.type === "text"; });
      if (!isText || !u.utteranceId) { return; }
      var txt = textOf(u.messages);
      if (voice.lead) { if (isLongText(txt, voice.lead)) { hideUtterance(u.utteranceId, 0); } }   /* the text arrived after the card of the same reply */
      else if (!voice.texts.some(function (t) { return t.id === u.utteranceId; })) { voice.texts.push({ id: u.utteranceId, text: txt }); }
    });
  }
  function voiceUpdate(utts) {
    if (!Array.isArray(utts)) { return; }
    utts.forEach(function (u) { if (u && u.utteranceId && voice.hidden[u.utteranceId]) { hideUtterance(u.utteranceId, 0); } });
  }
  /* The consumer is always on (registered as soon as the SDK element exists, not only on a bidi event: the bidi events can fire
     before the page listener is attached); it does nothing for a reply without display_text. */
  function registerVoice() {
    var el = chatEl();
    if (el && typeof el.registerMessageConsumer === "function" && !voice.registered) {
      try { el.registerMessageConsumer(voiceConsumer); voice.registered = true; } catch (err) { console.warn("[wgl] speech pilot consumer failed:", err); }
    }
  }
  function voiceStart() {
    registerVoice();
    voice.on = true;
  }
  function voiceStop() { voice.on = false; voice.texts = []; voice.lead = ""; }

  function onResponse(e) {
    try { normalizeMessages(e && e.detail && e.detail.messages); }
    catch (err) { console.warn("[wgl] could not normalize the reply:", err); }
    try { applyDisplayText(e && e.detail && e.detail.messages); }
    catch (err3) { console.warn("[wgl] speech pilot could not apply display_text (text stays visible):", err3); }
    try {
      var raw = e && e.detail && e.detail.raw;
      var outs = (raw && raw.outputs) || [];
      outs.forEach(function (o) {
        var calls = (o && o.toolCalls && o.toolCalls.toolCalls) || [];
        calls.forEach(function (c) {
          var name = lastSegment(c && c.tool);
          if (CARDS[name]) { renderTool(name, c.args); }
        });
      });
    } catch (err2) { console.warn("[wgl] tool-call fallback failed:", err2); }
  }

  /* ---- welcome rendered by the website when the chat opens (once per browser session) ---- */
  var WELCOME_KEY = "wgl-welcome-shown";
  var welcomeDone = false;
  var resetAt = 0;
  function reducedMotion() { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches); }
  function welcomeShown() {
    try { return window.sessionStorage.getItem(WELCOME_KEY) === "1"; } catch (e) { return welcomeDone; }
  }
  function setWelcomeShown(on) {
    welcomeDone = on;
    try { if (on) { window.sessionStorage.setItem(WELCOME_KEY, "1"); } else { window.sessionStorage.removeItem(WELCOME_KEY); } } catch (e) { /* storage blocked */ }
  }
  function isChatOpen() {
    var l = findLauncher();
    return !!(l && l.bubble && (l.bubble.expanded === true || isAlreadyOpen(l)));
  }
  function showWelcome(attempt) {
    if (PREVIEW || welcomeDone || welcomeShown()) { return; }
    attempt = attempt || 0;
    var el = chatEl();
    if (!el || typeof el.renderCustomText !== "function" || typeof el.renderCustomCard !== "function" || !el.presenter) {
      if (attempt < 20) { setTimeout(function () { showWelcome(attempt + 1); }, 250); }
      return;
    }
    setWelcomeShown(true);
    var page = pageKey(), lang = readLang(page);
    setVars({ page_context: window.wglPageContext || page, ui_language: lang });
    el.renderCustomText(WELCOME.pages[page][lang].greeting, true);
    setTimeout(function () {
      el.renderCustomCard([{ type: "custom_template", name: "cx-show-welcome-chips", id: "wgl-welcome-" + Date.now(), payload: { page: page, lang: lang, initial: lang } }]);
    }, reducedMotion() ? 0 : 380);
  }
  function onOpenChanged(e) {
    if (e && e.detail && e.detail.isOpen) { showWelcome(0); }
  }
  function onSessionReset() {
    /* "Start new chat": the transcript is cleared, so greet again; the init-time session start must not trigger this */
    if (!resetAt || Date.now() - resetAt > 8000) { return; }
    resetAt = 0;
    setWelcomeShown(false);
    setTimeout(function () { showWelcome(0); }, 400);
  }
  function watchResetButton() {
    var b = doc.querySelector("chat-reset-session-button");
    if (b && !b._wglWatched) {
      b._wglWatched = true;
      b.addEventListener("click", function () { resetAt = Date.now(); }, true);
    }
  }

  /* ---- Rev 29 chat text style (option A, "match the card text"): the bot text bubbles use the card typeface (Figtree),
     14.5px, line height 1.5, weight 400, and dollar amounts and dates inside them are bold. Page-only and cosmetic:
     the SDK has no bot-only size or line-height variable (the size variable is shared with the customer bubble), so a small
     stylesheet is added to the open shadow roots that hold a .bot-message bubble. Cards, chips, charts, customer bubbles,
     links and inputs are never touched; anything that throws leaves the text as it is. ---- */
  var TS_CSS = ".message.bot-message.bot-message{font-family:var(--chat-messenger-font-family,\"Figtree\",system-ui,sans-serif);font-size:14.5px;line-height:1.5;font-weight:400}" +
    ".message.bot-message.bot-message strong,.message.bot-message.bot-message b{font-weight:700}";
  var TS_AMOUNT = /\$\d[\d,]*(?:\.\d+)?|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.? \d{1,2}(?!\d)(?:st|nd|rd|th)?(?:, \d{4})?/g;
  var TS_SKIP = "strong,b,a,code,pre,input,textarea,button,select,h1,h2,h3,h4,h5,h6,[data-wgl-key]";
  var ts = { observed: [], timer: 0, scanTimer: 0 };
  function tsEmphasize(el) {
    try {
      var walker = doc.createTreeWalker(el, 4 /* NodeFilter.SHOW_TEXT */, null);
      var nodes = [], n;
      while ((n = walker.nextNode())) { nodes.push(n); }
      nodes.forEach(function (t) {
        var parent = t.parentElement;
        if (!parent || (parent.closest && parent.closest(TS_SKIP))) { return; }
        var text = t.nodeValue;
        TS_AMOUNT.lastIndex = 0;
        if (!text || !TS_AMOUNT.test(text)) { return; }
        TS_AMOUNT.lastIndex = 0;
        var frag = doc.createDocumentFragment(), last = 0, m;
        while ((m = TS_AMOUNT.exec(text))) {
          if (m.index > last) { frag.appendChild(doc.createTextNode(text.slice(last, m.index))); }
          var st = doc.createElement("strong");
          st.setAttribute("data-wgl-key", "1");
          st.textContent = m[0];
          frag.appendChild(st);
          last = m.index + m[0].length;
        }
        if (last < text.length) { frag.appendChild(doc.createTextNode(text.slice(last))); }
        parent.replaceChild(frag, t);
      });
    } catch (err) { /* leave the text alone */ }
  }
  function tsRoot(root) {
    try {
      var bubbles = root.querySelectorAll(".message.bot-message");
      if (!bubbles.length) { return; }
      if (!root.querySelector("style[data-wgl-text-style]")) {
        var st = doc.createElement("style");
        st.setAttribute("data-wgl-text-style", "1");
        st.textContent = TS_CSS;
        root.appendChild(st);
      }
      for (var i = 0; i < bubbles.length; i++) { tsEmphasize(bubbles[i]); }
    } catch (err) { /* cosmetic only */ }
  }
  function tsWalk(root) {
    var all = root.querySelectorAll("*");
    if (ts.observed.indexOf(root) < 0 && window.MutationObserver) {
      ts.observed.push(root);
      new MutationObserver(tsSchedule).observe(root, { childList: true, subtree: true, characterData: true });
    }
    tsRoot(root);
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.shadowRoot && !/^(wgl|cx)-/.test(e.localName)) { tsWalk(e.shadowRoot); }   /* the cards (wgl-*, cx-*) are never entered */
    }
  }
  function tsScan() {
    try {
      var el = chatEl();
      /* the embed puts <chat-messenger-chat-bubble> in the LIGHT DOM of <chat-messenger> (its utterances live in the bubble's shadow
         tree), so the walk starts at the element itself and also enters its own shadow root when it has one */
      if (el) { tsWalk(el); if (el.shadowRoot) { tsWalk(el.shadowRoot); } }
    } catch (err) { /* cosmetic only */ }
  }
  function tsSchedule() {
    if (ts.scanTimer) { return; }
    ts.scanTimer = setTimeout(function () { ts.scanTimer = 0; tsScan(); }, 60);
  }
  function textStyleStart() {
    if (PREVIEW || ts.timer) { return; }
    var n = 0;
    ts.timer = setInterval(function () {
      tsScan();
      if (++n > 120) { clearInterval(ts.timer); }   /* the observers take over once the shadow roots exist */
    }, 500);
    tsScan();
  }

  function wireSdk() {
    watchResetButton();
    if (!welcomeDone && !welcomeShown() && isChatOpen()) { showWelcome(0); }
    applyVariables();
    registerTools();
    registerVoice();
    textStyleStart();
  }

  /* ---------------------------------------------------------------- init */
  function init() {
    fab = doc.querySelector(".assistant-fab");
    toast = doc.querySelector(".assistant-toast");
    doc.querySelectorAll("[data-open-assistant]").forEach(function (el) {
      el.addEventListener("click", function (ev) { ev.preventDefault(); openAssistant(0); });
    });
    if (PREVIEW) { return; }
    syncPlaceholder();
    if (window.customElements && customElements.whenDefined) {
      customElements.whenDefined("chat-messenger").then(function () { syncPlaceholder(); wireSdk(); });
    }
    ["chat-messenger-loaded", "chat-messenger-context-registered", "chat-messenger-session-id-set",
     "chat-messenger-start-new-session", "chat-messenger-open-changed"].forEach(function (n) {
      window.addEventListener(n, function () { syncPlaceholder(); wireSdk(); });
    });
    window.addEventListener("chat-messenger-response-received", onResponse);
    window.addEventListener("chat-messenger-start-bidi", voiceStart);
    window.addEventListener("chat-messenger-connect-bidi", voiceStart);
    window.addEventListener("chat-messenger-disconnect-bidi", voiceStop);
    window.addEventListener("chat-messenger-open-changed", onOpenChanged);
    window.addEventListener("chat-messenger-start-new-session", onSessionReset);
    window.addEventListener("chat-messenger-session-ended", function () { setWelcomeShown(false); });
    window.addEventListener("chat-messenger-session-expired", function () { setWelcomeShown(false); });
    window.addEventListener("chat-messenger-user-input-entered", lockWelcomeCards);
    var n = 0;
    var t = setInterval(function () {
      syncPlaceholder(); wireSdk();
      if (++n > 40 || (registered && root.classList.contains("has-assistant-widget"))) { clearInterval(t); }
    }, 500);
  }

  if (doc.readyState === "loading") { doc.addEventListener("DOMContentLoaded", init); } else { init(); }
  window.openAssistant = function () { openAssistant(0); };

  /* Used by widget-preview.html and handy in the console: WGLCards.create("show_bill_trend_card", {...}) */
  /* Test hooks of the Rev 27 speech pilot (pure functions and the voice consumer). */
  window.WGLSpeech = { dupOf: dupOf, insertLeadBubble: insertLeadBubble, leadOf: leadOf, applyDisplayText: applyDisplayText, normalize: normalizeMessages, consumer: voiceConsumer, state: voice, start: voiceStart, stop: voiceStop };
  window.WGLTextStyle = { emphasize: tsEmphasize, scan: tsScan, css: TS_CSS, root: tsRoot };
  window.WGLCards = {
    tools: WGL_TOOLS,
    welcome: WELCOME,
    pageKey: pageKey,
    create: function (tool, args) {
      var el = doc.createElement("wgl-card");
      el.dfPayload = { tool: tool, args: args };
      return el;
    }
  };
})();
