/* ===========================================================================
 * probe_env.js —— 在「智算平台」的浏览器页面控制台里跑，拿到本环境的 SSH 端口与密码
 * ---------------------------------------------------------------------------
 * 用法
 *   1. Edge/Chrome 打开平台，进入你的**开发环境（Notebook / VSCode）页面**，
 *      地址栏形如  https://aics.cambricon.com:30443/...?ns=<命名空间>
 *   2. F12 → Console（控制台）→ 整个文件粘进去 → 回车
 *   3. 把打印出来的 5 行 AICS_* 抄进 aics.env
 *
 * 为什么必须在这里跑
 *   - 鉴权用的是页面 localStorage 里的令牌（放进 X-Auth-Token 头），
 *     这个令牌拿不出去，只能在页面上下文里用；
 *   - 平台 Web 会话本身认的是 cookie，和这个令牌是两套东西，别搞混。
 *
 * 这个脚本只读接口、不改任何东西。
 * =========================================================================== */
(async () => {
  'use strict';

  const log = (...a) => console.log('%c[AICS]', 'color:#0a0;font-weight:bold', ...a);
  const warn = (...a) => console.warn('%c[AICS]', 'color:#c60;font-weight:bold', ...a);
  const bad = (...a) => console.error('%c[AICS]', 'color:#c00;font-weight:bold', ...a);

  // ---------------------------------------------------------------- 1. ns
  const qs = new URLSearchParams(location.search);
  let ns = qs.get('ns');
  if (!ns) {
    // 有些页面把 ns 放在 hash 里，例如 /#/...?ns=xxx
    const m = location.href.match(/[?&#/]ns=([^&#/]+)/);
    if (m) ns = decodeURIComponent(m[1]);
  }
  if (!ns) {
    bad('地址栏里找不到 ns 参数。请确认你打开的是「开发环境」页面（URL 里通常有 ?ns=）。');
    bad('当前地址：' + location.href);
    return;
  }
  log('命名空间 ns =', ns);

  // ------------------------------------------------------------ 2. token
  const looksLikeJwt = (s) =>
    typeof s === 'string' && s.split('.').length === 3 &&
    s.length > 40 && /^[A-Za-z0-9_\-]+\./.test(s);

  let token = null, tokenKey = null;
  const prefer = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    const v = localStorage.getItem(k);
    if (looksLikeJwt(v)) {
      prefer.push([k, v]);
      if (/token|auth|jwt/i.test(k)) { token = v; tokenKey = k; break; }
    }
  }
  if (!token && prefer.length) { tokenKey = prefer[0][0]; token = prefer[0][1]; }

  if (!token) {
    bad('翻遍 localStorage 也没找到 JWT 形态的令牌。');
    bad('请确认已登录平台，并且是从平台页面（不是新开标签直接访问 API）执行本脚本。');
    try { log('localStorage 的键：', Object.keys(localStorage)); } catch (e) {}
    return;
  }
  log('令牌来自 localStorage["' + tokenKey + '"]，长度 ' + token.length);

  // ------------------------------------------------------------- 3. 请求
  const bases = [
    '/api/compute/v2/namespace/' + encodeURIComponent(ns) + '/notebook',
    '/api/compute/v2/namespace/' + encodeURIComponent(ns) + '/notebooks',
  ];

  const pick = (payload) => {
    // 兼容 {items:[...]} / {data:[...]} / {data:{items:[]}} / 直接是数组
    let list = payload;
    if (list && !Array.isArray(list)) list = list.items || list.data || list.list;
    if (list && !Array.isArray(list) && list.items) list = list.items;
    if (!Array.isArray(list)) return null;
    if (list.length === 1) return list[0];
    return list.find((it) => it && it.spec && it.spec.enableSSH) || list[0];
  };

  let item = null, usedBase = null, lastErr = null;
  for (const base of bases) {
    try {
      const resp = await fetch(base, {
        method: 'GET',
        credentials: 'include',
        headers: { 'X-Auth-Token': token, 'Accept': 'application/json' },
      });
      if (!resp.ok) {
        lastErr = base + ' → HTTP ' + resp.status;
        warn(lastErr);
        if (resp.status === 401) warn('  401 通常是令牌不对/过期：刷新页面重新登录，再跑一次本脚本。');
        continue;
      }
      const json = await resp.json();
      item = pick(json);
      if (item) { usedBase = base; break; }
      lastErr = base + ' → 返回里找不到 notebook 条目';
      warn(lastErr);
    } catch (e) {
      lastErr = base + ' → ' + e;
      warn(lastErr);
    }
  }

  if (!item) {
    bad('没能拿到环境信息。最后一次错误：' + lastErr);
    bad('可以打开 Network 面板，手工看一眼平台实际调的是哪个接口，再改 bases 数组。');
    return;
  }
  log('接口命中：' + usedBase);

  // ------------------------------------------------------------- 4. 解析
  const meta = item.metadata || {};
  const spec = item.spec || {};
  const status = item.status || {};
  const ports = status.port || {};
  const urls = status.urls || {};

  const port = ports.nodePort || ports.sshNodePort || ports.nodePortSSH;
  const password = spec.password;
  const user = 'root';

  log('环境 uid  :', meta.uid || '(无)');
  log('enableSSH :', spec.enableSSH);
  log('vscode url:', urls.vscode || '(无)');

  if (spec.enableSSH === false || (!port && spec.enableSSH === false)) {
    warn('这个环境没有开 SSH（spec.enableSSH = false）。');
    warn('SSH 通道用不了，只能退化成「网页 IDE + 浏览器自动化」。');
    warn('想用 SSH：新建环境时勾上 SSH 选项（创建后一般不能补开）。');
  }
  if (!port) {
    bad('返回里没找到 SSH 端口（status.port.nodePort）。');
    bad('把下面这段贴给 AI，让它按实际字段名再找一次：');
    console.log(JSON.stringify(item, null, 2));
    return;
  }
  if (!password) {
    bad('返回里没有 spec.password，没法用密码登录。');
    console.log(JSON.stringify(item, null, 2));
    return;
  }

  const host = location.hostname;   // 直接用当前域名，和 API 同源

  // ------------------------------------------------------------- 5. 输出
  const block = [
    'AICS_HOST=' + host,
    'AICS_PORT=' + port,
    'AICS_USER=' + user,
    'AICS_PASSWORD=' + password,
    'AICS_SOCKS=127.0.0.1:1080',
  ].join('\n');

  console.log('\n%c=========== 复制下面 5 行，存成 aics.env ===========',
              'color:#06c;font-weight:bold');
  console.log(block);
  console.log('%c====================================================',
              'color:#06c;font-weight:bold');

  console.log('\n本机 SOCKS5 还没起的话，先在本地终端跑（跳板机账号问课程助教）：');
  console.log('  ssh -N -D 127.0.0.1:1080 <跳板机用户>@<跳板机地址>');

  console.log('\n然后自检：');
  console.log('  python -m aicslab check');

  console.log('\n（备用）不走代理、直连试试，多半不通：');
  console.log('  ssh ' + user + '@' + host + ' -p ' + port);

  // 顺手把结果留在 window 上，方便下次直接取
  window.__AICS_ENV__ = {
    host, port, user, password,
    uid: meta.uid, ns,
    vscode: urls.vscode || null,
    enableSSH: spec.enableSSH,
  };
  log('结果也存到了 window.__AICS_ENV__，输入它可再看一次。');
})();
