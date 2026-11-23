export const reconcileViewTemplate = `
<section class="panel">
  <h2>仓间对账互证</h2>
  <p class="hint">在批口径不含已封批与作废；互证与封批态同源，甲已封批拒累加，乙影子草案待复核晋升。</p>
  <div class="form-row">
    <div><label>仓间编码</label><input v-model="reconCode"/></div>
    <div><label>批次互证编码</label><input v-model="proofCode"/></div>
  </div>
  <div class="actions">
    <button @click="loadRecon">对账单仓</button>
    <button class="secondary" @click="loadProof">批次互证</button>
    <button class="secondary" @click="loadReconAll">全仓对账</button>
    <button class="secondary" @click="pullProof">拉取互证</button>
  </div>
  <div v-if="reconOne" class="detail-box">
    <p>配额 {{ reconOne.quota_batches }} · 在批 {{ reconOne.active_batches }} · 已封批 {{ reconOne.sealed_batches }} · <span :class="reconOne.balanced ? 'ok' : 'bad'">{{ reconOne.balanced ? "平衡" : "失衡" }}</span></p>
  </div>
  <div v-if="proofOne" class="detail-box">
    <p>状态 {{ proofOne.status }} · 封批态 <span class="badge" :class="proofOne.sealed ? 'pass' : 'done'">{{ proofOne.seal_state }}</span> · 在批计 {{ proofOne.active_counted ? "是" : "否" }}</p>
    <p>路径 {{ proofOne.branch || "—" }} · 正式累计 {{ proofOne.formal_accumulated }} · 影子草案 {{ proofOne.draft_count }} · 开放游标 {{ proofOne.open_excursions }} · 温谱 {{ proofOne.spectrum_exports }}</p>
    <p v-if="proofOne.minutes">封批纪要（{{ proofOne.minutes.branch }}）{{ proofOne.minutes.statement }}</p>
  </div>
  <div v-if="proofDrafts.length" class="detail-box">
    <h3>待复核影子草案</h3>
    <table class="data-table compact">
      <thead><tr><th>草案</th><th>路径</th><th>状态</th><th></th></tr></thead>
      <tbody>
        <tr v-for="d in proofDrafts" :key="d.id">
          <td>#{{ d.id }}</td>
          <td>{{ d.mode }}</td>
          <td><span class="badge frozen">{{ d.state }}</span></td>
          <td><button class="mini-btn" @click="promoteDraft(d.id)">封批态复核晋升</button></td>
        </tr>
      </tbody>
    </table>
  </div>
  <table class="data-table">
    <thead><tr><th>仓间</th><th>区域</th><th>在批</th><th>已封批</th><th>余配额</th><th>平衡</th></tr></thead>
    <tbody>
      <tr v-for="r in reconAllItems" :key="r.chamber_code">
        <td>{{ r.chamber_code }}</td>
        <td>{{ r.region }}</td>
        <td>{{ r.active_batches }}</td>
        <td>{{ r.sealed_batches }}</td>
        <td>{{ r.quota_left }}</td>
        <td><span :class="r.balanced ? 'ok' : 'bad'">{{ r.balanced ? "是" : "否" }}</span></td>
      </tr>
    </tbody>
  </table>
</section>
`;
