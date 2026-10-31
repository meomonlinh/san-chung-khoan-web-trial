'use strict';

const $ = (id) => document.getElementById(id);
let me = null;
let market = null;
let meta = null;
let selectedSymbol = 'BTC';
let selectedCategory = 'Tất cả';
let socket = null;
let refreshTimer = null;
let loansCache = null;
let roleLabels = {owner:'OWNER', superadmin:'SPADMIN', admin:'ADMIN', coordinate:'TỌA ĐỘ', member:'THÀNH VIÊN'};
const assetsOrder = ['GOLD','SILVER','COPPER','PLATINUM','OIL','BRENT','GAS','BTC','ETH','SOL'];
const categoryLabels = {'Kim loại quý':'KIM LOẠI QUÝ','Năng lượng':'NĂNG LƯỢNG','Crypto Real':'CRYPTO REAL'};
const money = n => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n||0));
const pct = n => `${Number(n||0)>=0?'+':''}${Number(n||0).toFixed(2)}%`;
const number = n => new Intl.NumberFormat('vi-VN',{maximumFractionDigits:6}).format(Number(n||0));
const esc = s => String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isRole = (...roles) => !!me && roles.includes(me.role);
const canStaff = () => isRole('owner','superadmin','admin','coordinate');
const canManage = () => isRole('owner','superadmin','admin');
const canOwner = () => isRole('owner');

async function api(url, options={}) {
  const response = await fetch(url, {
    credentials:'same-origin',
    headers:{'Content-Type':'application/json', ...(options.headers||{})},
    ...options
  });
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch { data = {detail:raw || 'Phản hồi máy chủ không hợp lệ.'}; }
  if (!response.ok) throw new Error(data.detail || `Lỗi ${response.status}`);
  return data;
}
function toast(message, type='success') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = String(message);
  $('toastArea').appendChild(el);
  window.setTimeout(()=>el.remove(), 4800);
}
function showModal(title, html) {
  $('modalTitle').textContent = title;
  $('modalBody').innerHTML = html;
  $('modalBackdrop').classList.remove('hidden');
}
function closeModal() { $('modalBackdrop').classList.add('hidden'); $('modalBody').innerHTML=''; }
function setText(id, value) { const el=$(id); if(el) el.textContent = value ?? ''; }
function setHidden(id, hidden) { const el=$(id); if(el) el.classList.toggle('hidden', !!hidden); }
function formatDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('vi-VN');
}
function roleName(role) { return roleLabels[role] || String(role||'').toUpperCase(); }
function tierName(tier) {
  return ({bronze:'ĐỒNG',silver:'BẠC',gold:'VÀNG',platinum:'BẠCH KIM',diamond:'KIM CƯƠNG',ruby:'RUBY',emerald:'LỤC BẢO'})[tier] || 'CHƯA CÓ';
}
function tab(name) {
  document.querySelectorAll('.tab[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  document.querySelectorAll('.tab-page').forEach(p=>p.classList.toggle('hidden',p.id!==`${name}Tab`));
  if (name==='premium') loadPremium();
  if (name==='transfer') loadLoans();
  if (name==='chat') loadChat();
  if (name==='moderation') loadReports();
  if (name==='owner' && canOwner()) loadOwnerConsole();
}

$('modalClose').addEventListener('click', closeModal);
$('modalBackdrop').addEventListener('click', e=>{if(e.target===$('modalBackdrop')) closeModal();});
document.addEventListener('keydown', e=>{if(e.key==='Escape') closeModal();});
document.querySelectorAll('.tab[data-tab]').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
document.querySelectorAll('.pill[data-category]').forEach(b=>b.addEventListener('click',()=>{
  selectedCategory=b.dataset.category;
  document.querySelectorAll('.pill[data-category]').forEach(x=>x.classList.toggle('active',x===b));
  renderAssets();
}));

$('loginForm').addEventListener('submit', async e=>{
  e.preventDefault(); setText('loginError','');
  try {
    await api('/api/login',{method:'POST',body:JSON.stringify({username:$('username').value.trim(),password:$('password').value})});
    $('password').value='';
    await boot();
  } catch(err) { setText('loginError',err.message); }
});
$('logoutBtn').addEventListener('click', async()=>{
  try { await api('/api/logout',{method:'POST',body:'{}'}); } catch(err) { toast(err.message,'error'); }
  disconnectSocket(); clearInterval(refreshTimer); me=null;
  $('appView').classList.add('hidden'); $('loginView').classList.remove('hidden'); $('password').value='';
});
$('inboxBtn').addEventListener('click',openInbox);
$('forecastBtn').addEventListener('click',openForecast);
$('pauseBtn').addEventListener('click',togglePause);
$('buyBtn').addEventListener('click',()=>trade('buy'));
$('sellBtn').addEventListener('click',()=>trade('sell'));
$('historyBtn').addEventListener('click',openHistory);
$('loansBtn').addEventListener('click',()=>{tab('transfer');loadLoans();});
$('purchasePremiumBtn').addEventListener('click',purchasePremium);
$('giftPremiumBtn').addEventListener('click',giftPremium);
$('specialGrantBtn').addEventListener('click',specialMembership);
$('ownerEmeraldSelfBtn').addEventListener('click',()=>grantEmeraldSelf());
$('transferBtn').addEventListener('click',transferMoney);
$('refreshChatBtn').addEventListener('click',loadChat);
$('chatForm').addEventListener('submit',sendChat);
$('profileForm').addEventListener('submit',saveProfile);
$('saveSettingsBtn').addEventListener('click',saveSettings);
$('requestAdminBtn').addEventListener('click',requestAdmin);
$('sendReportBtn').addEventListener('click',sendReport);
$('refreshReportsBtn').addEventListener('click',loadReports);
$('applyPenaltyBtn').addEventListener('click',applyPenalty);
$('adminCashBtn').addEventListener('click',adjustAdminCash);
$('banBtn').addEventListener('click',banAccount);
$('backupBtn').addEventListener('click',downloadBackup);
$('createAccountBtn').addEventListener('click',createOwnerAccount);
$('reloadAccountsBtn').addEventListener('click',loadOwnerAccounts);
$('saveLimitsBtn').addEventListener('click',saveRoleLimits);
$('endSupportBtn').addEventListener('click',endSupport);
$('reloadAdminRequestsBtn').addEventListener('click',loadAdminRequests);
$('reloadPremiumRequestsBtn').addEventListener('click',loadPremiumRequests);
$('reloadAuditBtn').addEventListener('click',loadAudit);

async function boot() {
  try {
    const health = await api('/api/health');
    if (health.trial_mode === 'true') setHidden('trialNotice', false);
  } catch (err) { console.warn('Health check unavailable', err); }
  try {
    me = await api('/api/v2/me');
    meta = await api('/api/v2/meta');
  } catch(err) {
    $('appView').classList.add('hidden'); $('loginView').classList.remove('hidden');
    if (err.message && !/đăng nhập/i.test(err.message)) setText('loginError',err.message);
    return;
  }
  document.title='SÀN CHỨNG KHOÁN';
  $('loginView').classList.add('hidden'); $('appView').classList.remove('hidden');
  renderIdentity();
  try { await refreshPortfolio(); market=await api('/api/market'); renderMarket(market); } catch(err) { toast(err.message,'error'); }
  connectSocket();
  try { await loadProfile(); } catch(err) { console.warn(err); }
  refreshTimer=window.setInterval(async()=>{if(!me)return;try{await refreshPortfolio(); await refreshMe();}catch(err){console.warn(err);}},8000);
  if (canOwner()) { loadOwnerConsole().catch(e=>toast(e.message,'error')); }
}
function renderIdentity() {
  if (!me) return;
  $('userBadge').textContent=`${me.display_name} · ${roleName(me.role)} · ID ${me.account_id}`;
  setHidden('pauseBtn', !canOwner());
  setHidden('specialPremiumPanel', !(canOwner() || isRole('superadmin')));
  setHidden('ownerEmeraldSelfBtn', !canOwner());
  setHidden('staffTools', !canStaff());
  setHidden('adminCashBtn', !canManage());
  document.querySelectorAll('.owner-only').forEach(el=>el.classList.toggle('hidden',!canOwner()));
  document.querySelectorAll('.tab.owner-only').forEach(el=>el.classList.toggle('hidden',!canOwner()));
  $('chatRoom').querySelector('option[value="qtv"]').disabled=!canStaff();
  $('chatRoom').querySelector('option[value="members"]').disabled=!(canStaff() || !!me.premium_tier);
  setText('profileAccountInfo',`@${me.username} · ID ${me.account_id} · ${roleName(me.role)} · ${tierName(me.premium_tier)}`);
  $('requestAdminBtn').disabled = me.role!=='member' || me.admin_request_status==='pending';
  if (me.admin_request_status==='pending') $('requestAdminBtn').textContent='YÊU CẦU ADMIN ĐANG CHỜ DUYỆT';
}
function connectSocket() {
  disconnectSocket();
  const protocol=location.protocol==='https:'?'wss':'ws';
  socket=new WebSocket(`${protocol}://${location.host}/ws`);
  socket.onmessage=e=>{try{const msg=JSON.parse(e.data);if(msg.type==='market'){market=msg.data;renderMarket(market);}}catch(err){console.warn(err);}};
  socket.onclose=()=>{if(me)window.setTimeout(()=>{if(me)connectSocket();},3000);};
  socket.onerror=()=>socket.close();
}
function disconnectSocket(){if(socket){try{socket.onclose=null;socket.close();}catch{}socket=null;}}

function renderAssets() {
  if(!market)return;
  const list=$('assetList'); list.innerHTML='';
  const filtered=assetsOrder.filter(sym=>market.assets[sym] && (selectedCategory==='Tất cả'||market.assets[sym].category===selectedCategory));
  setText('assetCount',`${filtered.length} mã`);
  filtered.forEach(sym=>{
    const a=market.assets[sym];
    const row=document.createElement('button'); row.className=`asset-row ${sym===selectedSymbol?'selected':''}`;
    const symbol=document.createElement('span');symbol.className='symbol';symbol.textContent=sym;
    const price=document.createElement('span');price.className='price';price.textContent=money(a.price);
    const change=document.createElement('span');change.className=`change ${a.change_pct>=0?'positive':'negative'}`;change.textContent=pct(a.change_pct);
    row.append(symbol,price,change);row.addEventListener('click',()=>{selectedSymbol=sym;renderMarket(market);});list.appendChild(row);
  });
}
function renderMarket(data) {
  if(!data)return;
  const a=data.assets[selectedSymbol]; if(!a)return;
  setText('marketStatus',data.paused?'TOÀN SÀN ĐANG TẠM DỪNG':data.market_status);
  setText('marketTag',data.paused?'TẠM DỪNG':(a.is_open?'ĐANG GIAO DỊCH':'ĐANG NGHỈ'));
  setText('selectedCategory',categoryLabels[a.category]||a.category.toUpperCase());
  setText('selectedName',`${selectedSymbol} / USD · ${a.name}`);
  setText('selectedPrice',money(a.price)); $('selectedPrice').style.color=a.change_pct>=0?'#23a89a':'#ef5350';
  setText('selectedChange',pct(a.change_pct)); $('selectedChange').className=`price-change ${a.change_pct>=0?'positive':'negative'}`;
  const vals=a.history?.length?a.history:[a.price];
  setText('chartHigh',`HIGH: ${money(Math.max(...vals))}`);setText('chartLow',`LOW: ${money(Math.min(...vals))}`);
  const ma=n=>vals.slice(-n).reduce((s,v)=>s+v,0)/Math.max(1,vals.slice(-n).length);
  const mas=[5,10,20,50,200].map(n=>({n,v:ma(n)}));
  const colors={5:'#f4d000',10:'#f05b9a',20:'#20d0df',50:'#9a79db',200:'#fff'};
  $('maValues').innerHTML=`<span>Giá: ${money(a.price)}</span>`+mas.map(item=>`<span style="color:${colors[item.n]}">MA${item.n}: ${money(item.v)}</span>`).join('');
  setText('analysisText',data.paused?'Toàn bộ thị trường đang tạm dừng theo lệnh Owner.':(!a.is_open?'Tài sản truyền thống đang nghỉ; Crypto chạy 24/7.':`Giá ${selectedSymbol} đang ${a.change_pct>=0?'tăng':'giảm'} ${pct(a.change_pct)} so với nhịp gần nhất.`));
  setText('marketClock',new Date(data.server_time).toLocaleTimeString('vi-VN'));
  renderAssets();drawChart(vals,mas);
  $('pauseBtn').textContent=data.paused?'CHẠY TOÀN SÀN':'DỪNG TOÀN SÀN';
  $('buyBtn').disabled=data.paused||!a.is_open; $('sellBtn').disabled=data.paused||!a.is_open;
  setText('forecastHint',me?.premium_tier?`Quyền dự báo: ${tierName(me.premium_tier)} · Mức thời gian theo gói.`:'Dự báo theo quyền tài khoản và cấp Premium.');
}
function drawChart(values,mas) {
  const canvas=$('priceChart'); if(!canvas)return;
  const rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  const w=Math.max(10,rect.width),h=Math.max(10,rect.height);
  canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
  const pad={l:16,r:12,t:20,b:18},pw=w-pad.l-pad.r,ph=h-pad.t-pad.b;
  for(let i=0;i<=5;i++){const y=pad.t+i*ph/5;ctx.strokeStyle='#1c2a3b';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();}
  for(let i=0;i<=9;i++){const x=pad.l+i*pw/9;ctx.beginPath();ctx.moveTo(x,pad.t);ctx.lineTo(x,h-pad.b);ctx.stroke();}
  const all=[...values,...mas.map(m=>m.v)],min=Math.min(...all)*.997,max=Math.max(...all)*1.003,span=max-min||1;
  const xAt=i=>pad.l+i*(pw/Math.max(1,values.length-1)),yAt=v=>pad.t+(max-v)/span*ph;
  ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(xAt(i),yAt(v)):ctx.moveTo(xAt(i),yAt(v)));ctx.lineTo(xAt(values.length-1),h-pad.b);ctx.lineTo(xAt(0),h-pad.b);ctx.closePath();ctx.fillStyle='rgba(255,159,10,.10)';ctx.fill();
  ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(xAt(i),yAt(v)):ctx.moveTo(xAt(i),yAt(v)));ctx.strokeStyle='#ff9f0a';ctx.lineWidth=2;ctx.stroke();
  const colors={5:'#f4d000',10:'#f05b9a',20:'#20d0df',50:'#9a79db',200:'#fff'};
  for(const n of [5,10,20,50,200]){ctx.beginPath();values.forEach((v,i)=>{const sample=values.slice(Math.max(0,i-n+1),i+1),avg=sample.reduce((s,x)=>s+x,0)/sample.length;i?ctx.lineTo(xAt(i),yAt(avg)):ctx.moveTo(xAt(i),yAt(avg));});ctx.strokeStyle=colors[n];ctx.lineWidth=n===200?1:1.3;ctx.stroke();}
}
async function refreshPortfolio() {
  const p=await api('/api/portfolio');
  setText('cashBalance',money(p.balance));setText('holdingsValue',money(p.holdings_value));setText('netWorth',money(p.net_worth));
  setText('exchangeDebt',money(me?.exchange_debt||0));
  const rows=Object.entries(p.holdings||{}).filter(([,q])=>Number(q)>0);
  $('holdingsList').innerHTML=rows.length?rows.map(([s,q])=>`<div class="holding-row"><span>${esc(s)} · ${number(q)}</span><b>${money(Number(q)*(market?.assets?.[s]?.price||0))}</b></div>`).join(''):'<p class="muted tiny">Chưa sở hữu tài sản nào.</p>';
}
async function refreshMe(){me=await api('/api/v2/me');renderIdentity();}
async function trade(side){
  const quantity=Number($('quantity').value);
  try{const result=await api('/api/v2/trade',{method:'POST',body:JSON.stringify({side,symbol:selectedSymbol,quantity})});toast(`${result.message} ${number(result.quantity)} ${result.symbol} · ${money(result.total)}`);await refreshMe();await refreshPortfolio();}
  catch(err){toast(err.message,'error');}
}
async function togglePause(){
  if(!canOwner())return;
  try{const paused=!(market?.paused);await api('/api/admin/market/pause',{method:'POST',body:JSON.stringify({paused})});toast(paused?'Đã dừng toàn bộ thị trường.':'Đã bật lại toàn bộ thị trường.');market=await api('/api/market');renderMarket(market);}
  catch(err){toast(err.message,'error');}
}
async function openForecast(){
  try{
    const ruby = me?.premium_tier === 'ruby';
    let rows=[];
    if(canOwner()) rows=(await api('/api/admin/forecast')).items;
    else {const seconds=ruby?3:null;const res=await api('/api/v2/forecast'+(seconds?`?seconds=${seconds}`:''));rows=res.items.map(x=>({...x,label:`Dự báo ${res.seconds} giây`,seconds:res.seconds}));}
    const rubyControl=ruby?'<label>Thời gian dự báo RUBY<select id="rubyForecastSeconds"><option value="1">1 giây</option><option value="3" selected>3 giây</option><option value="5">5 giây</option></select></label>':'';
    const html=`<p class="muted tiny">Dự báo được tạo từ cùng lịch giá mô phỏng trên máy chủ, không phải cam kết giá thực tế.</p>${rubyControl}<label>Tài sản<select id="forecastSymbol">${assetsOrder.map(s=>`<option value="${s}">${s}</option>`).join('')}</select></label><div id="forecastRows" class="data-list" style="margin-top:14px"></div>`;
    showModal(canOwner()?'DỰ BÁO OWNER · 6 MỐC THỜI GIAN':'DỰ BÁO THEO GÓI',html);
    const render=()=>{const sym=$('forecastSymbol').value;const selected=rows.filter(r=>r.symbol===sym);$('forecastRows').innerHTML=selected.map(r=>`<div class="holding-row"><span>${esc(r.label||'Dự báo')}</span><b>${r.paused?'SÀN ĐANG DỪNG':r.error?esc(r.error):`${pct(r.change_pct)} · ${money(r.price)}`}</b></div>`).join('')||'<p class="muted">Không có dữ liệu dự báo.</p>';};
    $('forecastSymbol').addEventListener('change',render);
    if(ruby)$('rubyForecastSeconds').addEventListener('change',async()=>{try{const res=await api(`/api/v2/forecast?seconds=${$('rubyForecastSeconds').value}`);rows=res.items.map(x=>({...x,label:`Dự báo ${res.seconds} giây`,seconds:res.seconds}));render();}catch(e){toast(e.message,'error');}});
    render();
  }catch(err){toast(err.message,'error');}
}
async function openInbox(){
  try{
    const res=await api('/api/inbox');
    const html=res.items?.length?res.items.map(x=>`<article class="message-card"><b>${esc(x.title)}</b><p>${esc(x.body)}</p><small class="muted">${formatDate(x.created_at)}</small></article>`).join(''):'<p class="muted">Hòm thư đang trống.</p>';
    setText('inboxCount','0');showModal('HÒM THƯ THÔNG BÁO',html);
  }catch(err){toast(err.message,'error');}
}
async function openHistory(){
  try{
    const p=await api('/api/portfolio');
    const rows=(p.trades||[]).map(t=>`<tr><td>${esc(t.side)}</td><td>${esc(t.symbol)}</td><td>${number(t.quantity)}</td><td>${money(t.price)}</td><td>${money(t.total_cents/100)}</td><td>${formatDate(t.created_at)}</td></tr>`).join('');
    showModal('LỊCH SỬ GIAO DỊCH',rows?`<div class="table-wrap"><table class="modal-table"><thead><tr><th>Loại</th><th>Mã</th><th>Khối lượng</th><th>Giá</th><th>Tổng</th><th>Thời điểm</th></tr></thead><tbody>${rows}</tbody></table></div>`:'<p class="muted">Chưa có giao dịch.</p>');
  }catch(err){toast(err.message,'error');}
}

async function loadPremium(){
  try{
    const res=await api('/api/v2/premium');
    setText('premiumStatus',`${res.active.label}${res.active.expires_at?` · hết hạn ${formatDate(res.active.expires_at)}`:''}`);
    const plans={three_day:'3 ngày',month:'1 tháng',year:'1 năm'};
    const keys=['bronze','silver','gold','platinum','diamond'];
    $('premiumGrid').innerHTML=keys.map(tier=>{
      const item=res.catalog[tier];
      return `<article class="premium-card"><div class="tier-title">${esc(item.label)}</div><div class="tier-value">${money(item.daily)}</div><small>thưởng cơ bản mỗi ngày · dự báo ${item.forecast}s</small><hr>${Object.entries(item.prices).map(([p,cost])=>`<div class="price-line"><span>${plans[p]}</span><b>${money(cost)}</b></div>`).join('')}</article>`;
    }).join('') + `<article class="premium-card special-tier"><div class="tier-title">RUBY</div><div class="tier-value">${money(res.catalog.ruby.daily)}</div><small>Không bán · Owner cấp / SPADMIN xin duyệt</small></article>` + `<article class="premium-card special-tier emerald-tier"><div class="tier-title">LỤC BẢO</div><div class="tier-value">${money(res.catalog.emerald.daily)}</div><small>Đặc quyền Owner · bảo vệ 80% khoản lỗ theo lệnh mua hợp lệ</small></article>`;
    setHidden('specialPremiumPanel',!(canOwner()||isRole('superadmin')));
    $('specialTier').querySelector('option[value="emerald"]').disabled=!canOwner();
    $('specialTier').querySelector('option[value="ruby"]').disabled=!(canOwner()||isRole('superadmin'));
    if(canOwner()) await loadPremiumRequests();
  }catch(err){toast(err.message,'error');}
}
async function purchasePremium(){
  try{
    const res=await api('/api/v2/premium/purchase',{method:'POST',body:JSON.stringify({tier:$('premiumTier').value,plan:$('premiumPlan').value})});
    toast('Đã mua/gia hạn Premium.');await refreshMe();await refreshPortfolio();await loadPremium();
  }catch(err){toast(err.message,'error');}
}
async function giftPremium(){
  const payload={recipient_id:$('giftRecipientId').value.trim(),recipient_name:$('giftRecipientName').value.trim(),tier:$('giftTier').value,plan:$('giftPlan').value};
  try{const res=await api('/api/v2/premium/gift',{method:'POST',body:JSON.stringify(payload)});toast(`Đã tặng Premium. Tổng thanh toán ${money(res.total)} (gồm phụ phí).`);await refreshMe();await refreshPortfolio();}
  catch(err){toast(err.message,'error');}
}
async function specialMembership(){
  const tier=$('specialTier').value,target_id=$('specialTargetId').value.trim();
  try{
    if(tier==='ruby'&&isRole('superadmin')){const res=await api('/api/v2/premium/ruby-request',{method:'POST',body:JSON.stringify({target_id,tier,permanent:false})});toast(`Đã gửi yêu cầu RUBY #${res.request_id} chờ Owner duyệt.`);}
    else {const res=await api('/api/v2/premium/special',{method:'POST',body:JSON.stringify({target_id,tier,permanent:false})});toast(res.message||`Đã cấp ${tierName(tier)}.`);}
    await refreshMe();await loadPremium();
  }catch(err){toast(err.message,'error');}
}
async function grantEmeraldSelf(){
  try{await api('/api/v2/premium/special',{method:'POST',body:JSON.stringify({target_id:me.account_id,tier:'emerald',permanent:true})});toast('Owner đã kích hoạt LỤC BẢO vĩnh viễn.');await refreshMe();await refreshPortfolio();await loadPremium();}
  catch(err){toast(err.message,'error');}
}
async function loadPremiumRequests(){
  if(!canOwner())return;
  try{
    const res=await api('/api/v2/owner/premium-requests');
    $('premiumRequestsBox').innerHTML=res.items.length?`<h4>Yêu cầu RUBY chờ duyệt</h4>${res.items.map(r=>`<div class="request-row"><span>#${r.id} · ${esc(r.requester_name)} tặng ${esc(r.target_name)} (${r.target_id})</span><span><button class="small-button" data-premium-review="${r.id}" data-approve="true">Duyệt</button> <button class="small-button danger" data-premium-review="${r.id}" data-approve="false">Từ chối</button></span></div>`).join('')}`:'<p class="muted tiny">Không có yêu cầu RUBY đang chờ.</p>';
    $('premiumRequestsBox').querySelectorAll('[data-premium-review]').forEach(b=>b.addEventListener('click',async()=>{try{await api(`/api/v2/owner/premium-requests/${b.dataset.premiumReview}/review`,{method:'POST',body:JSON.stringify({approve:b.dataset.approve==='true'})});toast('Đã xử lý yêu cầu.');loadPremiumRequests();}catch(e){toast(e.message,'error');}}));
  }catch(err){$('premiumRequestsBox').textContent=err.message;}
}

async function transferMoney(){
  const payload={amount:$('transferAmount').value,recipient_id:$('transferRecipientId').value.trim(),recipient_name:$('transferRecipientName').value.trim(),action:$('transferAction').value};
  try{
    const res=await api('/api/v2/transfer',{method:'POST',body:JSON.stringify(payload)});
    toast(res.action==='loan'?`Đã ghi nhận khoản cho nợ ${money(res.amount)}.`:`Đã chuyển ${money(res.amount)}; người nhận thực nhận ${money(res.received)}; phí ${money(res.tax)}.`);
    $('transferAmount').value='';await refreshMe();await refreshPortfolio();await loadLoans();
  }catch(err){toast(err.message,'error');}
}
async function loadLoans(){
  try{
    const res=await api('/api/v2/loans');loansCache=res;
    const item=(x,type)=>`<div class="holding-row"><span>${type==='lent'?'Cho: ':'Nhận từ: '}${esc(type==='lent'?x.borrower_name:x.lender_name)} · ID ${esc(type==='lent'?x.borrower_account_id:x.lender_account_id)}<small class="muted"> · ${esc(x.status)}</small></span><b>${money(x.remaining_cents/100)} / ${money(x.original_cents/100)}</b></div>`;
    const lent=res.lent.map(x=>item(x,'lent')).join('')||'<p class="muted tiny">Chưa có khoản cho nợ.</p>';
    const borrowed=res.borrowed.map(x=>item(x,'borrowed')).join('')||'<p class="muted tiny">Chưa có khoản đang nợ.</p>';
    $('loanLists').innerHTML=`<div><h4>ĐÃ CHO NỢ</h4>${lent}</div><div><h4>ĐANG NỢ</h4>${borrowed}</div>`;
  }catch(err){$('loanLists').textContent=err.message;}
}

async function loadChat(){
  const room=$('chatRoom').value;
  try{
    const res=await api(`/api/v2/chat/${encodeURIComponent(room)}`);
    $('chatMessages').innerHTML=res.items.length?res.items.map(msg=>`<article class="chat-message"><div><b>${esc(msg.display_name)}</b><small class="muted"> · ${esc(msg.role_label)} · ${formatDate(msg.created_at)}</small></div><p>${esc(msg.body)}</p></article>`).join(''):'<p class="muted">Phòng chat chưa có tin nhắn.</p>';
    $('chatMessages').scrollTop=$('chatMessages').scrollHeight;
  }catch(err){$('chatMessages').innerHTML=`<p class="error">${esc(err.message)}</p>`;}
}
async function sendChat(e){
  e.preventDefault();const text=$('chatText').value.trim();if(!text)return;
  try{await api(`/api/v2/chat/${encodeURIComponent($('chatRoom').value)}`,{method:'POST',body:JSON.stringify({text})});$('chatText').value='';await loadChat();}
  catch(err){toast(err.message,'error');}
}
async function loadProfile(){
  me=await api('/api/v2/me');
  $('profileName').value=me.display_name||'';$('profileAge').value=me.profile_age||'';$('profileLocation').value=me.profile_location||'';$('profileBio').value=me.profile_bio||'';
  $('soundMuted').checked=!!me.sound_muted;$('soundVolume').value=me.sound_volume??25;
  renderIdentity();
}
async function saveProfile(e){
  e.preventDefault();
  try{me=await api('/api/v2/profile',{method:'POST',body:JSON.stringify({display_name:$('profileName').value,age:$('profileAge').value,location:$('profileLocation').value,bio:$('profileBio').value})});toast('Đã lưu hồ sơ.');renderIdentity();}
  catch(err){toast(err.message,'error');}
}
async function saveSettings(){
  try{await api('/api/v2/settings',{method:'POST',body:JSON.stringify({sound_muted:$('soundMuted').checked,sound_volume:Number($('soundVolume').value)})});toast('Đã lưu cài đặt thông báo.');}
  catch(err){toast(err.message,'error');}
}
async function requestAdmin(){
  try{await api('/api/v2/admin/request',{method:'POST',body:'{}'});toast('Đã gửi yêu cầu xin quyền Admin.');await refreshMe();}
  catch(err){toast(err.message,'error');}
}

async function sendReport(){
  const payload={target_id:$('reportTargetId').value.trim(),target_name:$('reportTargetName').value.trim(),reason:$('reportReason').value.trim(),request_ban:$('reportRequestBan').checked};
  try{const res=await api('/api/v2/moderation/reports',{method:'POST',body:JSON.stringify(payload)});toast(`Đã gửi báo cáo #${res.report_id}.`);$('reportReason').value='';await loadReports();}
  catch(err){toast(err.message,'error');}
}
async function loadReports(){
  try{
    const res=await api('/api/v2/moderation/reports');
    $('reportsList').innerHTML=res.items.length?res.items.map(r=>`<article class="report-row"><div><b>Hồ sơ #${r.id} · ${esc(r.status)}</b><p>${esc(r.author_name)} → ${esc(r.target_name)} · ID ${esc(r.target_account_id)}</p><p>${esc(r.reason)}</p><small class="muted">${formatDate(r.created_at)}</small></div>${canOwner()&&r.status==='pending'?`<div class="row-actions"><button class="small-button" data-report-review="${r.id}" data-approve="true">Duyệt</button><button class="small-button danger" data-report-review="${r.id}" data-approve="false">Từ chối</button></div>`:''}</article>`).join(''):'<p class="muted">Chưa có hồ sơ báo cáo.</p>';
    $('reportsList').querySelectorAll('[data-report-review]').forEach(b=>b.addEventListener('click',async()=>{const note=prompt('Ghi chú xử lý (có thể bỏ trống):')||'';try{await api(`/api/v2/moderation/reports/${b.dataset.reportReview}/decision`,{method:'POST',body:JSON.stringify({approve:b.dataset.approve==='true',note})});toast('Đã xử lý báo cáo.');loadReports();}catch(e){toast(e.message,'error');}}));
    if(canStaff()) await loadPenaltyRequests();
  }catch(err){$('reportsList').textContent=err.message;}
}
async function adjustAdminCash(){
  if(!canManage())return;
  const raw=prompt('Nhập số USD điều chỉnh cho ví của chính bạn: số dương để cộng, số âm để trừ. Giới hạn mỗi lần: 9.999.999.999 USD.');
  if(raw===null)return;
  const amount=Number(raw);
  if(!Number.isFinite(amount)||amount===0||Math.abs(amount)>9_999_999_999){toast('Số tiền không hợp lệ hoặc vượt giới hạn.','error');return;}
  try{
    await api('/api/v2/admin/cash',{method:'POST',body:JSON.stringify({delta:amount})});
    await refreshMe();await refreshPortfolio();toast('Đã điều chỉnh ví quản trị.');
  }catch(err){toast(err.message,'error');}
}

async function applyPenalty(){
  const payload={target_id:$('penaltyTargetId').value.trim(),amount_spec:$('penaltyAmount').value.trim(),reason:$('penaltyReason').value.trim()};
  try{const res=await api('/api/v2/admin/penalty',{method:'POST',body:JSON.stringify(payload)});toast(res.pending_superadmin?`Đã gửi yêu cầu phạt RUBY #${res.request_id} chờ SPADMIN.`:`Đã áp dụng khoản phạt ${money(res.amount)}.`);await loadPenaltyRequests();}
  catch(err){toast(err.message,'error');}
}
async function loadPenaltyRequests(){
  try{
    const res=await api('/api/v2/admin/penalty-requests');
    $('penaltyRequests').innerHTML=res.items.length?`<h4>Yêu cầu phạt RUBY chờ xử lý</h4>${res.items.map(r=>`<div class="request-row"><span>#${r.id} · ${esc(r.actor_name)} → ${esc(r.target_name)} · ${esc(r.amount_spec)}</span><span><button class="small-button" data-penalty-review="${r.id}" data-approve="true">Duyệt</button><button class="small-button danger" data-penalty-review="${r.id}" data-approve="false">Từ chối</button></span></div>`).join('')}`:'<p class="muted tiny">Không có yêu cầu phạt RUBY đang chờ.</p>';
    $('penaltyRequests').querySelectorAll('[data-penalty-review]').forEach(b=>b.addEventListener('click',async()=>{const note=prompt('Ghi chú (tùy chọn):')||'';try{await api(`/api/v2/admin/penalty-requests/${b.dataset.penaltyReview}/review`,{method:'POST',body:JSON.stringify({approve:b.dataset.approve==='true',note})});toast('Đã xử lý yêu cầu phạt.');loadPenaltyRequests();}catch(e){toast(e.message,'error');}}));
  }catch(err){$('penaltyRequests').textContent=err.message;}
}
async function banAccount(){
  const payload={target_id:$('banTargetId').value.trim(),target_name:$('banTargetName').value.trim(),reason:$('banReason').value.trim(),banned:$('banAction').value==='true'};
  try{await api('/api/v2/moderation/ban',{method:'POST',body:JSON.stringify(payload)});toast(payload.banned?'Đã khóa tài khoản.':'Đã mở khóa tài khoản.');}
  catch(err){toast(err.message,'error');}
}

async function createOwnerAccount(){
  const username=$('newUsername').value.trim(),password=$('newPassword').value,display_name=$('newDisplayName').value.trim();
  if(!username||!password){toast('Nhập tên đăng nhập và mật khẩu tạm.','error');return;}
  try{const res=await api('/api/v2/owner/accounts',{method:'POST',body:JSON.stringify({username,password,display_name})});toast(`Đã tạo ${res.account.username} · ID ${res.account.account_id}. Hãy chia sẻ mật khẩu tạm qua kênh riêng.`);$('newUsername').value='';$('newPassword').value='';$('newDisplayName').value='';await loadOwnerAccounts();}
  catch(err){toast(err.message,'error');}
}
async function loadOwnerAccounts(){
  if(!canOwner())return;
  try{
    const res=await api('/api/v2/owner/accounts');
    $('limitSuperadmin').value=res.limits.superadmin??5;$('limitAdmin').value=res.limits.admin??10;$('limitCoordinate').value=res.limits.coordinate??20;
    $('accountsTable').innerHTML=`<div class="table-wrap"><table class="modal-table"><thead><tr><th>Tài khoản</th><th>ID</th><th>Vai trò</th><th>Số dư</th><th>Premium</th><th>Trạng thái / thao tác</th></tr></thead><tbody>${res.items.map(a=>`<tr><td><b>${esc(a.display_name)}</b><br><small>@${esc(a.username)}</small></td><td>${esc(a.account_id)}</td><td>${roleName(a.role)}</td><td>${money(a.balance)}</td><td>${tierName(a.premium_tier)}${a.premium_expires_at?`<br><small>${formatDate(a.premium_expires_at)}</small>`:''}</td><td>${a.banned?'ĐANG KHÓA':'Hoạt động'}<br><div class="row-actions"><button class="small-button" data-account-action="promote" data-id="${esc(a.account_id)}">Thăng cấp</button><button class="small-button" data-account-action="demote" data-id="${esc(a.account_id)}">Hạ cấp</button><button class="small-button" data-account-action="superadmin" data-id="${esc(a.account_id)}">SPADMIN</button><button class="small-button" data-account-action="cash" data-id="${esc(a.account_id)}">± USD</button><button class="small-button" data-account-action="ruby" data-id="${esc(a.account_id)}">RUBY</button><button class="small-button" data-account-action="emerald" data-id="${esc(a.account_id)}">LỤC BẢO</button><button class="small-button danger" data-account-action="revoke" data-id="${esc(a.account_id)}">Thu hồi Premium</button><button class="small-button" data-account-action="support" data-id="${esc(a.account_id)}">Hỗ trợ</button></div></td></tr>`).join('')}</tbody></table></div>`;
    $('accountsTable').querySelectorAll('[data-account-action]').forEach(b=>b.addEventListener('click',()=>ownerAccountAction(b.dataset.accountAction,b.dataset.id)));
  }catch(err){$('accountsTable').textContent=err.message;}
}
async function ownerAccountAction(action,id){
  try{
    if(action==='promote'||action==='demote'||action==='superadmin'){
      const direction=action==='superadmin'?'grant_superadmin':action;
      const res=await api(`/api/v2/owner/accounts/${id}/role`,{method:'POST',body:JSON.stringify({direction})});toast(`Đã đổi vai trò thành ${roleName(res.role)}.`);
    } else if(action==='cash'){
      const raw=prompt('Nhập số USD điều chỉnh: số dương để cộng, số âm để trừ.');if(raw===null)return;
      const amount=Number(raw);if(!Number.isFinite(amount)||amount===0){toast('Số tiền không hợp lệ.','error');return;}
      await api(`/api/v2/owner/accounts/${id}/cash`,{method:'POST',body:JSON.stringify({delta:amount})});toast('Đã điều chỉnh số dư.');
    } else if(action==='ruby'||action==='emerald'){
      const tier=action==='ruby'?'ruby':'emerald';
      const permanent=action==='emerald'&&confirm('Cấp LỤC BẢO vĩnh viễn? Chỉ Owner được cấp vĩnh viễn cho chính mình. Chọn Cancel để cấp gói 1 ngày.');
      const r=await api('/api/v2/premium/special',{method:'POST',body:JSON.stringify({target_id:id,tier,permanent})});toast(r.message||'Đã cấp gói đặc biệt.');
    } else if(action==='revoke'){
      if(!confirm('Thu hồi toàn bộ Premium của tài khoản này?'))return;
      await api(`/api/v2/owner/accounts/${id}/membership/revoke`,{method:'POST',body:'{}'});toast('Đã thu hồi Premium.');
    } else if(action==='support'){
      const r=await api(`/api/v2/owner/support/start?account_id=${encodeURIComponent(id)}`,{method:'POST',body:'{}'});toast(`Đã mở phiên hỗ trợ có ghi nhật ký cho ${r.target.display_name}.`);await loadSupportState();
    }
    await loadOwnerAccounts();await refreshMe();await refreshPortfolio();
  }catch(err){toast(err.message,'error');}
}
async function saveRoleLimits(){
  const limits={superadmin:Number($('limitSuperadmin').value),admin:Number($('limitAdmin').value),coordinate:Number($('limitCoordinate').value)};
  try{await api('/api/v2/owner/role-limits',{method:'POST',body:JSON.stringify({limits})});toast('Đã lưu hạn mức chức vụ.');}
  catch(err){toast(err.message,'error');}
}
async function loadSupportState(){
  if(!canOwner())return;
  try{const res=await api('/api/v2/owner/support');setText('supportState',res.active?`Đang hỗ trợ: ${res.target.display_name} · @${res.target.username} · ID ${res.target.account_id} · Số dư ${money(res.target.balance_cents/100)}`:'Không có phiên hỗ trợ đang hoạt động.');setHidden('endSupportBtn',!res.active);}
  catch(err){setText('supportState',err.message);}
}
async function endSupport(){
  try{await api('/api/v2/owner/support/end',{method:'POST',body:'{}'});toast('Đã kết thúc phiên hỗ trợ.');await loadSupportState();}
  catch(err){toast(err.message,'error');}
}
async function loadAdminRequests(){
  if(!canOwner())return;
  try{const res=await api('/api/v2/owner/admin-requests');$('adminRequests').innerHTML=res.items.length?res.items.map(r=>`<div class="request-row"><span>#${r.id} · ${esc(r.display_name)} (@${esc(r.username)}) · ID ${esc(r.account_id)}</span><span><button class="small-button" data-admin-review="${r.id}" data-approve="true">Duyệt</button><button class="small-button danger" data-admin-review="${r.id}" data-approve="false">Từ chối</button></span></div>`).join(''):'<p class="muted tiny">Không có yêu cầu Admin đang chờ.</p>';
    $('adminRequests').querySelectorAll('[data-admin-review]').forEach(b=>b.addEventListener('click',async()=>{try{await api(`/api/v2/owner/admin-requests/${b.dataset.adminReview}/review`,{method:'POST',body:JSON.stringify({approve:b.dataset.approve==='true',note:''})});toast('Đã xử lý yêu cầu Admin.');loadAdminRequests();loadOwnerAccounts();}catch(e){toast(e.message,'error');}}));
  }catch(err){$('adminRequests').textContent=err.message;}
}
async function loadAudit(){
  if(!canOwner())return;
  try{const res=await api('/api/v2/owner/audit');$('auditList').innerHTML=res.items.length?`<table class="modal-table"><thead><tr><th>Thời điểm</th><th>Người thực hiện</th><th>Vai trò</th><th>Thao tác</th><th>Mục tiêu</th><th>Chi tiết</th></tr></thead><tbody>${res.items.map(a=>`<tr><td>${formatDate(a.created_at)}</td><td>${esc(a.actor_username)}</td><td>${roleName(a.actor_role)}</td><td>${esc(a.action)}</td><td>${esc(a.target_username)}</td><td>${esc(a.details)}</td></tr>`).join('')}</tbody></table>`:'<p class="muted">Nhật ký trống.</p>';}
  catch(err){$('auditList').textContent=err.message;}
}
async function loadOwnerConsole(){
  if(!canOwner())return;
  await Promise.allSettled([loadOwnerAccounts(),loadSupportState(),loadAdminRequests(),loadPremiumRequests(),loadAudit()]);
}
async function downloadBackup(){
  try{
    const response=await fetch('/api/v2/owner/backup',{credentials:'same-origin'});
    if(!response.ok){const data=await response.json().catch(()=>({detail:'Không thể sao lưu.'}));throw new Error(data.detail||'Không thể sao lưu.');}
    const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='sanh-chung-khoan-backup.json';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);toast('Đã tải bản sao lưu dữ liệu.');
  }catch(err){toast(err.message,'error');}
}
window.addEventListener('resize',()=>{if(market)renderMarket(market);});
window.addEventListener('beforeunload',disconnectSocket);
boot();
