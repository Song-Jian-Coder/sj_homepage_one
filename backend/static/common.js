/* ═══════════════════════════════════════════════════════════════
 * common.js —— 看板页面共享层
 *
 * 存在的原因（审查报告 §8.12 / §9.3-1）：
 *   项目里有正确的转义实现（原 industry.html:724 的 esc），但三处页面各写一份，
 *   fundflow.html 与 admin.html 完全没有，industry.html 自己也漏了 3 个插值点 ——
 *   于是「行业名」这条从明文 HTTP 上游进库的字符串成了存储型 XSS 入口
 *   （审查报告 6.2.1，P0）。修法不是「记得每处都转义」，而是让所有人用同一个。
 *
 * 用法（在页面 <head> 中、页面自身脚本之前引入）：
 *   <script src="common.js"></script>
 *
 * 约定：本文件不含任何 DOM 依赖，可被 7 个页面共同使用，也可在 node 下单测。
 * ═══════════════════════════════════════════════════════════════ */
(function (global) {
  'use strict';

  /* ───────── 1. 转义 ─────────
   * esc() 覆盖文本与**属性**两种上下文：`"` 与 `'` 都必须转，
   * 否则 title="${x}" 这类属性上下文仍可被闭合（审查报告 6.2.1 特别指出
   * fundflow.html:478 是属性上下文，比纯文本更危险）。
   */
  var ESC_MAP = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;', '`': '&#96;'
  };
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"'`]/g, function (c) { return ESC_MAP[c]; });
  }

  /* 属性上下文专用（语义等价于 esc，保留别名以便自解释） */
  function escAttr(s) { return esc(s); }

  /* URL 查询参数 */
  function escUrl(s) { return encodeURIComponent(String(s == null ? '' : s)); }

  /* ───────── 2. 安全写入 ─────────
   * 结构性解法：不拼字符串，直接写 textContent。
   * `setText(el, v)` 有 XSS 免疫力 —— 因为它根本不经过 HTML 解析器。
   */
  function setText(el, v) {
    if (el) el.textContent = (v == null ? '' : String(v));
    return el;
  }

  /* ───────── 3. 数字 / 金额 / 日期 ─────────
   * 单位口径与后端一致：亿元、列表 2 位小数、成交额 1 位小数（docs/03 §6）。
   * 统一 `null`/`undefined`/`NaN` 的兜底写法 —— 原实现三处三种（审查报告 5.1.5 的前端对应项）。
   */
  function fnum(v, dflt) {
    if (v == null) return dflt === undefined ? null : dflt;
    var n = Number(v);
    return isFinite(n) ? n : (dflt === undefined ? null : dflt);
  }
  function yi(v) {                       /* 亿元，带符号，2 位小数 */
    var n = fnum(v, 0);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '亿';
  }
  function yiPlain(v) {                  /* 亿元，无符号 */
    var n = fnum(v, 0);
    return n.toFixed(2) + '亿';
  }
  function fmtAmt(v) {                   /* 亿元，带符号，1 位小数（表格 / 图注用） */
    var n = fnum(v, 0);
    return (n >= 0 ? '+' : '') + n.toFixed(1);
  }
  /* 兼容别名：fundflow.html 沿用 fmtYi 这个名字（= 带符号 + 2 位小数的亿元） */
  function fmtYi(v) { return yi(v); }
  function pctStr(v) {                   /* 百分比，带符号，2 位小数；v 已是百分数 */
    var n = fnum(v, 0);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }
  function numToFixed(v, dp, dflt) {
    var n = fnum(v, null);
    return n === null ? (dflt === undefined ? '—' : dflt) : n.toFixed(dp);
  }

  /* ───────── 4. 涨跌色 / 档位阈值 ─────────
   * 审查报告 6.7：同一套数据在 fundflow.html 用 0.02、industry.html 与 admin.html
   * 用 0.005，同一个值在两张页面会被判成不同档位。这里收敛为一个常量。
   */
  var THRESHOLD = 0.005;                 /* 亿元：低于此值视为平盘 */

  function cls(v, t) {
    var n = fnum(v, 0);
    var th = (t === undefined) ? THRESHOLD : t;
    return n > th ? 'up' : (n < -th ? 'down' : 'flat');
  }
  /* admin.html 用 pos/neg/flat 三态（管理台正号为绿，与看板相反） */
  function clsAdmin(v, t) {
    var n = fnum(v, null);
    if (n === null) return 'flat';
    var th = (t === undefined) ? THRESHOLD : t;
    return n > th ? 'pos' : (n < -th ? 'neg' : 'flat');
  }

  /* ───────── 5. API 封装 ─────────
   * 审查报告 6.4 / 6.5：原 industry.html 同时存在 _api() 与 apiGet()，
   * 错误契约不同；且两者都只认 {code,msg}，而 FastAPI 的参数校验失败返回的是
   * **422 {"detail":[...]}**（docs/03:35）—— 于是文档承诺的 422 形态在
   * 前端退化成一句无信息的「数据接口错误」。这里合一，并显式处理两种形态。
   */
  function apiBase() {
    /* 双击本地文件打开时无处可发请求，回落到本机后端；正常托管时同源。 */
    return (typeof location !== 'undefined' && location.protocol === 'file:')
      ? 'http://127.0.0.1:8000' : '';
  }

  /* 把 FastAPI 的 422 明细压成一行可读文案 */
  function detailText(detail) {
    if (!Array.isArray(detail)) return '';
    return detail.map(function (d) {
      var loc = (d && d.loc ? d.loc.filter(function (x) { return x !== 'query' && x !== 'body'; }).join('.') : '');
      var msg = (d && d.msg) ? d.msg : '';
      var typ = (d && d.type) ? d.type : '';
      return (loc ? loc + ': ' : '') + (msg || typ);
    }).filter(Boolean).join('；');
  }

  function ApiError(message, opts) {
    var e = new Error(message);
    e.name = 'ApiError';
    e.status = (opts && opts.status) || 0;
    e.code = (opts && opts.code != null) ? opts.code : null;
    e.traceId = (opts && opts.traceId) || '';
    e.detail = (opts && opts.detail) || null;
    return e;
  }

  /* api(path, {headers, method, body, raw}) → Promise<data>
   * - 成功（code === 0）解析出 data；
   * - 业务错误（code !== 0）抛 ApiError，携带 code/msg/traceId，便于「凭 traceId 对账」；
   * - HTTP 422 / 4xx / 5xx 抛 ApiError，携带可读的校验明细。 */
  function api(path, opts) {
    opts = opts || {};
    var url = apiBase() + path;
    var init = { method: opts.method || 'GET' };
    if (opts.headers) init.headers = opts.headers;
    if (opts.body !== undefined) {
      init.headers = Object.assign({ 'Content-Type': 'application/json' }, init.headers || {});
      init.body = (typeof opts.body === 'string') ? opts.body : JSON.stringify(opts.body);
    }
    return fetch(url, init).then(function (r) {
      return r.text().then(function (txt) {
        var j = null;
        try { j = txt ? JSON.parse(txt) : null; } catch (e) { j = null; }

        if (!r.ok) {
          var msg422 = (j && j.detail) ? detailText(j.detail) : '';
          throw ApiError(
            'HTTP ' + r.status + (msg422 ? ' · ' + msg422 : '') + '（请求 ' + path + '）',
            { status: r.status, detail: j && j.detail }
          );
        }
        if (!j) throw ApiError('响应不是合法 JSON（请求 ' + path + '）', { status: r.status });

        /* 业务外壳：code 字段存在即按业务码判定 */
        if (j.code !== undefined && j.code !== 0) {
          throw ApiError(j.msg || ('业务错误 code=' + j.code),
            { status: r.status, code: j.code, traceId: j.traceId, detail: j.detail });
        }
        return (j.data !== undefined) ? j.data : j;
      });
    });
  }

  /* 兼容旧调用名 */
  var apiGet = function (path, opts) { return api(path, opts); };

  /* ───────── 6. 其它 ───────── */
  /* 代码归一：ts_code ↔ 6 位代码 */
  function tsCode(code) {
    if (!code) return code;
    if (/\.(SH|SZ|BJ)$/.test(code)) return code;
    if (/^[69]/.test(code)) return code + '.SH';
    if (/^[023]/.test(code)) return code + '.SZ';
    return code + '.BJ';
  }
  function bareCode(code) { return String(code || '').replace(/\.(SH|SZ|BJ)$/, ''); }

  /* 防抖：搜索输入与树重建使用同一个实现（审查报告 6.3） */
  function debounce(fn, wait) {
    var t = null;
    return function () {
      var self = this, args = arguments;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  /* 数字千分位（日志 / 概览用） */
  function thousands(n) {
    var v = fnum(n, 0);
    return v.toLocaleString('zh-CN');
  }

  global.SW = {
    esc: esc, escAttr: escAttr, escUrl: escUrl, setText: setText,
    fnum: fnum, yi: yi, yiPlain: yiPlain, fmtAmt: fmtAmt, fmtYi: fmtYi,
    pctStr: pctStr, numToFixed: numToFixed,
    cls: cls, clsAdmin: clsAdmin, THRESHOLD: THRESHOLD,
    api: api, apiGet: apiGet, apiBase: apiBase, ApiError: ApiError, detailText: detailText,
    tsCode: tsCode, bareCode: bareCode, debounce: debounce, thousands: thousands
  };

  /* 同时挂到全局的常用短名，尽量减少对既有代码的侵入式改写。
     注意 api/apiGet 也必须挂全局：industry.html / fundflow.html 里大量
     直接调用 `apiGet(...)`，而它们是普通 <script>（非 module），
     不会自动拿到 IIFE 作用域里的名字。
     —— 这一点是 2026-10-06 用无头 Edge 实测发现的：只挂 SW.apiGet 会
     在运行期抛「apiGet is not defined」，页面静默变成错误空态。 */
  global.esc = esc;
  global.escAttr = escAttr;
  global.escUrl = escUrl;
  global.yi = yi;
  global.fmtYi = fmtYi;
  global.fmtAmt = fmtAmt;
  global.cls = cls;
  global.pctStr = pctStr;
  global.numToFixed = numToFixed;
  global.fnum = fnum;
  global.tsCode = tsCode;
  global.bareCode = bareCode;
  global.api = api;
  global.apiGet = apiGet;
  global.setSafeText = setText;
}(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this)));
