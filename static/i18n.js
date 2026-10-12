(function () {
  const ZH = {
    "Smart routing dashboard": "智能配送调度台", "Delivery Agent": "智能配送助手",
    "Use my location": "使用我的位置", "Live GPS: off": "实时定位：关", "Live GPS: on": "实时定位：开",
    "Paste a WeChat message here…": "在此粘贴微信消息…", "Handle message": "AI 处理消息",
    "Customer name": "客户姓名", "Phone (optional)": "电话（选填）",
    "Address / notes (optional) — e.g. Bldg 5, floor 3": "地址/备注（选填）— 如 5号楼3层",
    "Tap the map to set the location": "点击地图设置位置", "Latitude": "纬度", "Longitude": "经度",
    "Promise in minutes (optional)": "承诺送达时间（分钟，选填）", "Add order": "添加订单",
    "Stops": "站点", "Finish": "完成时间", "Route": "路线", "updated": "更新于",
    "No orders yet.": "暂无订单。", "Tap the map to add one": "点击地图添加订单",
    "Order added — stop #": "订单已添加 — 第", " in the route": " 站",
    "Navigate": "导航", "Share": "分享", "promised": "承诺", "LATE": "迟到",
    "Logout": "退出", "Orders": "订单",
    "AI inbox": "AI 收件箱", "Retrain AI": "重新训练 AI", "Paste a WeChat message above": "在上方粘贴微信消息",
    "Was I right?": "我判断对了吗？", "It means:": "实际意思：", "Learned!": "已学习！",
    "examples collected": "条训练样本", "press": "点击", "below to use them": "让 AI 学会",
    "% sure": "% 把握", "Customer": "客户", "Driver": "司机",
    "New order": "新订单", "Question": "询问", "Delay": "延误", "Not home": "不在家",
    "Order form filled": "已自动填写订单", "tap the map": "点击地图", "to set the location": "设置位置",
    "Wants to cancel": "想要取消", "Cancel the order": "取消订单", "'s order": " 的订单",
    "is asking about the order": "在询问订单", "Copy reply": "复制回复",
    "Driver delivered": "司机已送达", "Mark as delivered": "标记为已送达",
    "The driver is running late": "司机将会迟到", "is not home": "不在家",
    "Not sure": "不确定", "please check it yourself": "请人工确认",
    "order cancelled": "订单已取消", "order delivered": "订单已送达",
    "Copied — paste it in WeChat": "已复制 — 去微信粘贴", "Training…": "训练中…",
    "AI retrained on": "AI 已重新训练，样本数", "taught by you": "条由你教的",
    "New order:": "新订单：", "Paste a message first.": "请先粘贴一条消息。",
    "Please enter the customer name.": "请输入客户姓名。",
    "Tap the map to set the location, and enter a name.": "请点击地图设置位置并输入姓名。",
    "All orders": "全部订单", "Dashboard": "调度台", "Total": "总数", "Pending": "待配送",
    "Today:": "今天：", "Today": "今天", "All": "全部", "avg": "平均", "min per order": "分钟/单",
    "Search by name, phone or note…": "按姓名、电话或备注搜索…", "Edit": "编辑",
    "No orders here.": "暂无订单。", "pending": "待配送", "cancelled": "已取消",
    "Delivered!": "已送达！", "Delivered": "已送达", "delivered": "已送达",
    "Cancel": "取消", "Right": "对", "Fix": "纠正", "Done": "完成", "Call": "电话", "km": "公里", "Km": "公里",
    "Loading…": "加载中…", "Order not found": "未找到订单",
    "Please check your tracking link.": "请检查您的追踪链接。", "Enjoy,": "请享用，",
    "Order cancelled": "订单已取消", "Hi ": "您好 ", "Your order is on the way": "您的订单正在配送中",
    "Estimated arrival": "预计到达", "Position": "位置", "Queue": "排队",
    "You're next!": "下一个就是您！", "stops before you": "站在您之前", "stop before you": "站在您之前",
    "Promised by": "承诺送达", "Updates automatically": "自动更新",
    "Connection problem": "连接问题", "Retrying…": "重试中…",
    " examples": " 条", "low confidence": "把握不足", "order not found": "未找到订单",
    "unknown intent": "未知意图", "ETA": "预计到达",
    "Clear demo": "清除演示", "demo orders added": "个演示订单已添加",
    "watch the route": "看看路线", "demo orders removed": "个演示订单已删除", "Demo": "演示",
    "Simulate": "模拟配送", "All orders delivered!": "所有订单已送达！", "Try:": "试试：",
    "Next stop": "下一站", "Up next": "接下来", "No more stops": "没有更多站点了",
    "Great job!": "干得好！", "Start GPS": "开启定位", "GPS on — tap to stop": "定位中 — 点击停止",
    "GPS stopped": "定位已停止", "left": "剩余", "finish": "完成", "Sent": "已发送",
    "Enter the dashboard password": "请输入调度台密码", "Password": "密码", "Log in": "登录",
    "Wrong password.": "密码错误。", "Too many attempts. Wait 10 minutes.": "尝试次数过多，请等待10分钟。",
    "Stats - Delivery Agent": "统计 - 智能配送助手", "Stats": "统计",
    "This week": "本周", "orders delivered": "已送达订单",
    "Median time": "中位时长", "minutes per order": "分钟/单",
    "On time": "准时率", "of promised orders": "占承诺订单",
    "Deliveries — last 7 days": "近 7 天送达量", "Busiest hours": "最忙时段",
  };
  const PAIRS = Object.entries(ZH).sort((a, b) => b[0].length - a[0].length);
  const tr = s => { for (const [en, zh] of PAIRS) if (s.includes(en)) s = s.split(en).join(zh); return s; };

  let lang = 'en';
  try { lang = localStorage.getItem('lang') || ((navigator.language || '').startsWith('zh') ? 'zh' : 'en'); } catch (e) {}

  function translate(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const p = n.parentNode && n.parentNode.nodeName;
      if (p === 'SCRIPT' || p === 'STYLE' || p === 'TEXTAREA') continue;
      const t = tr(n.nodeValue);
      if (t !== n.nodeValue) n.nodeValue = t;
    }
    if (root.querySelectorAll) root.querySelectorAll('[placeholder]').forEach(el => {
      const t = tr(el.placeholder); if (t !== el.placeholder) el.placeholder = t;
    });
  }

  function start() {
    const btn = document.createElement('button');
    btn.textContent = lang === 'zh' ? 'EN' : '中文';
    btn.title = 'Language / 语言';
    btn.style.cssText = 'position:fixed;top:10px;right:10px;z-index:10000;width:auto;margin:0;' +
      'padding:6px 12px;font-size:13px;border-radius:20px;border:1px solid var(--accent);' +
      'background:var(--surface);color:var(--text);cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4)';
    btn.onclick = () => {
      try { localStorage.setItem('lang', lang === 'zh' ? 'en' : 'zh'); } catch (e) {}
      location.reload();
    };
    document.body.appendChild(btn);
    if (lang !== 'zh') return;
    document.documentElement.lang = 'zh';
    translate(document.body);
    document.title = tr(document.title);
    new MutationObserver(muts => muts.forEach(m => {
      if (m.type === 'characterData') {
        const t = tr(m.target.nodeValue); if (t !== m.target.nodeValue) m.target.nodeValue = t;
      } else m.addedNodes.forEach(nd => {
        if (nd.nodeType === 3) { const t = tr(nd.nodeValue); if (t !== nd.nodeValue) nd.nodeValue = t; }
        else if (nd.nodeType === 1 && nd.nodeName !== 'TEXTAREA') translate(nd);
      });
    })).observe(document.body, {childList: true, subtree: true, characterData: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
