import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { getBlackboard, addBlackboardHint } from '../api/client';
import type { BlackboardSnapshot, BlackboardNode as BBNode } from '../types';
import type { JSX } from 'react';
import cytoscape, { type Core, type ElementDefinition, type StylesheetJson } from 'cytoscape';
import elk from 'cytoscape-elk';

cytoscape.use(elk);

interface Props {
  sessionId: string | null;
  open: boolean;
  onClose: () => void;
}

// ── 节点视觉规格 ──
interface Vis {
  color: string;
  fill: string;
  stroke: string;
  icon: string;
  label: string;
}

const KIND_VIS: Record<string, Vis> = {
  origin:     { color: '#e5e7eb', fill: '#374151',  stroke: '#6b7280', icon: '⊙', label: '起点' },
  goal:       { color: '#e9d5ff', fill: '#4c1d95',  stroke: '#a855f7', icon: '◎', label: '目标' },
  target:     { color: '#fed7aa', fill: '#7c2d12',  stroke: '#f97316', icon: '⌖', label: '端点' },
  hypothesis: { color: '#99f6e4', fill: '#134e4a',  stroke: '#14b8a6', icon: '?', label: '假设' },
  intent:     { color: '#a5f3fc', fill: '#164e63',  stroke: '#0891b2', icon: '◇', label: '调度' },
  action:     { color: '#bfdbfe', fill: '#1e3a8a',  stroke: '#3b82f6', icon: '▶', label: '动作' },
  evidence:   { color: '#bfdbfe', fill: '#1e40af',  stroke: '#60a5fa', icon: '≡', label: '证据' },
  fact:       { color: '#dbeafe', fill: '#1e40af',  stroke: '#3b82f6', icon: '✓', label: '结论' },
  handoff:    { color: '#fde68a', fill: '#78350f',  stroke: '#d97706', icon: '📋', label: '交接' },
  hint:       { color: '#fde68a', fill: '#78350f',  stroke: '#d97706', icon: '!', label: '提示' },
};

const EDGE_COLORS: Record<string, string> = {
  supports: '#4ade80',
  refutes: '#f87171',
  supersedes: '#6b7280',
  leads_to: '#2dd4bf',
  precondition: '#5eead4',
  derived: '#60a5fa',
  branch: '#a78bfa',
};

// 隐藏脚手架根 intent（"达成目标…" 语义与 goal 重复）
function isHiddenRoot(n: BBNode): boolean {
  return n.kind === 'intent' && n.discovered_by === 'reason' && n.label.startsWith('达成目标');
}

// 截断标签
function truncateLabel(text: string, max = 20): string {
  const t = (text || '').replace(/\s+/g, ' ').trim();
  if (!t) return '';
  const chars = [...t];
  let w = 0;
  let out = '';
  for (const ch of chars) {
    w += ch.charCodeAt(0) > 255 ? 1.6 : 1;
    if (w > max) { out += '…'; break; }
    out += ch;
  }
  return out;
}

// 构建 Cytoscape 元素
function buildElements(snap: BlackboardSnapshot): ElementDefinition[] {
  const hiddenIds = new Set(snap.nodes.filter(isHiddenRoot).map(n => n.id));
  const originId = snap.nodes.find(n => n.kind === 'origin')?.id || '';

  const visibleNodes = snap.nodes.filter(n => !hiddenIds.has(n.id));

  const elements: ElementDefinition[] = [];

  // 节点
  for (const n of visibleNodes) {
    const vis = KIND_VIS[n.kind] || KIND_VIS.fact;
    const polarity = n.properties?.polarity as string | undefined;
    let stroke = vis.stroke;
    if (n.kind === 'evidence' && polarity === 'refutes') stroke = '#f87171';
    else if (n.kind === 'evidence' && polarity === 'supports') stroke = '#4ade80';

    const dimmed = n.status === 'superseded' || n.status === 'refuted';
    const labelPrefix = vis.icon ? `${vis.icon} ` : '';

    elements.push({
      group: 'nodes',
      data: {
        id: n.id,
        label: labelPrefix + truncateLabel(n.label, 22),
        fullLabel: n.label,
        kind: n.kind,
        status: n.status || '',
        color: vis.color,
        background: vis.fill,
        borderColor: stroke,
        dimmed: dimmed ? 0.35 : 1,
        active: n.status === 'active',
      },
    });
  }

  // 边：隐藏根的边锚定到 origin
  for (const e of snap.edges) {
    let src = e.source;
    let tgt = e.target;
    if (hiddenIds.has(src)) src = originId;
    if (hiddenIds.has(tgt)) tgt = originId;
    if (!src || !tgt || src === tgt) continue;
    // 确保两端都在可见节点中
    if (!visibleNodes.find(n => n.id === src) || !visibleNodes.find(n => n.id === tgt)) continue;

    const color = EDGE_COLORS[e.relation] || '#64748b';
    const dashed = e.relation === 'refutes' || e.relation === 'supersedes';

    elements.push({
      group: 'edges',
      data: {
        id: e.id,
        source: src,
        target: tgt,
        relation: e.relation,
        color,
        lineStyle: dashed ? 'dashed' : 'solid',
        opacity: e.relation === 'supersedes' ? 0.4 : 0.7,
      },
    });
  }

  return elements;
}

// ELK 布局配置
function elkLayout(): any {
  return {
    name: 'elk',
    elk: {
      algorithm: 'layered',
      'elk.direction': 'RIGHT',
      'elk.layered.spacing.nodeNodeBetweenLayers': 60,
      'elk.spacing.nodeNode': 40,
      'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.layered.spacing.edgeNodeBetweenLayers': 30,
      'elk.padding': '[top=20,left=20,bottom=20,right=20]',
    },
    fit: true,
    padding: 30,
  };
}

// Cytoscape 样式
function graphStyles(): StylesheetJson {
  return [
    {
      selector: 'node',
      style: {
        shape: 'round-rectangle',
        width: 168,
        height: 54,
        'background-color': 'data(background)',
        'border-color': 'data(borderColor)',
        'border-width': 2,
        label: 'data(label)',
        color: 'data(color)',
        'font-size': 11,
        'font-weight': 600,
        'text-wrap': 'wrap',
        'text-max-width': '150px',
        'text-valign': 'center',
        'text-halign': 'center',
        opacity: 'data(dimmed)' as any,
        'overlay-opacity': 0,
      },
    },
    {
      selector: 'node[?active]',
      style: {
        'border-width': 3,
      },
    },
    {
      selector: 'node.is-selected',
      style: {
        'border-width': 3,
        'border-color': '#fbbf24',
        'overlay-color': '#fbbf24',
        'overlay-opacity': 0.25,
        'overlay-padding': 8,
      },
    },
    {
      selector: '.is-dimmed-node',
      style: { opacity: 0.3 },
    },
    {
      selector: 'edge',
      style: {
        width: 1.8,
        'line-color': 'data(color)',
        'target-arrow-color': 'data(color)',
        'line-style': 'data(lineStyle)' as any,
        'target-arrow-shape': 'triangle',
        'curve-style': 'taxi',
        'taxi-turn': 20,
        'taxi-direction': 'auto',
        'arrow-scale': 0.85,
        opacity: 'data(opacity)' as any,
        'overlay-opacity': 0,
      },
    },
    {
      selector: 'edge.is-active-edge',
      style: {
        width: 2.5,
        label: 'data(relation)',
        'font-size': 9,
        'font-weight': 600,
        color: '#94a3b8',
        'text-background-color': '#0f172a',
        'text-background-opacity': 0.85,
        'text-background-padding': '2px',
        'text-rotation': 'autorotate',
      },
    },
  ];
}

export function AttackMap({ sessionId, open, onClose }: Props): JSX.Element | null {
  const [snapshot, setSnapshot] = useState<BlackboardSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showHintForm, setShowHintForm] = useState(false);
  const [hintLabel, setHintLabel] = useState('');
  const [hintDetail, setHintDetail] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const cyRef = useRef<Core | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const onSelectRef = useRef(setSelectedId);

  const fetchBoard = useCallback(async () => {
    if (!sessionId) return;
    try {
      const bb = await getBlackboard(sessionId);
      setSnapshot(bb);
      setError('');
    } catch (e: any) {
      setError(e.message || '无法获取黑板');
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (open && sessionId) {
      setLoading(true);
      fetchBoard();
    }
  }, [open, sessionId, fetchBoard]);

  useEffect(() => {
    if (!open || !autoRefresh || !sessionId) return;
    const t = setInterval(fetchBoard, 3000);
    return () => clearInterval(t);
  }, [open, autoRefresh, sessionId, fetchBoard]);

  async function handleAddHint() {
    if (!sessionId || !hintLabel.trim()) return;
    try {
      await addBlackboardHint(sessionId, { label: hintLabel.trim(), detail: hintDetail.trim() });
      setHintLabel('');
      setHintDetail('');
      setShowHintForm(false);
      await fetchBoard();
    } catch (e: any) {
      setError(e.message || '注入 Hint 失败');
    }
  }

  const handoffs = useMemo(
    () => (snapshot?.nodes || [])
      .filter(n => n.kind === 'handoff')
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || '')),
    [snapshot],
  );
  const selected = selectedId && snapshot
    ? snapshot.nodes.find(n => n.id === selectedId) || null
    : null;
  const stats = snapshot?.stats;

  // Cytoscape 初始化 / 更新
  useEffect(() => {
    if (!open || !snapshot) return;
    // 等 containerRef 在下一帧可用
    const initCy = () => {
      if (!containerRef.current) return;
      const elements = buildElements(snapshot);
      cyRef.current?.destroy();
      if (!elements.length) return;

      const cy = cytoscape({
        container: containerRef.current,
        elements,
        style: graphStyles(),
        minZoom: 0.15,
        maxZoom: 2.5,
        wheelSensitivity: 0.35,
        boxSelectionEnabled: false,
        selectionType: 'single',
      });
      cyRef.current = cy;

      cy.on('tap', 'node', (evt) => onSelectRef.current(evt.target.id()));
      cy.on('tap', (evt) => {
        if (evt.target === cy) onSelectRef.current(null);
      });

      // 运行布局：优先 ELK，失败则回退 cose
      try {
        const layout = cy.layout(elkLayout());
        layout.on('layoutstop', () => {
          cy.resize();
          cy.fit(undefined, 40);
        });
        layout.on('layouterror', () => {
          const fb = cy.layout({
            name: 'cose',
            animate: false,
            nodeRepulsion: 8000,
            idealEdgeLength: 120,
            nodeOverlap: 20,
            padding: 30,
            fit: true,
          });

          fb.run();
        });
        layout.run();
      } catch (e) {
        console.warn('ELK exception, fallback cose:', e);
        cy.layout({
          name: 'cose',
          animate: false,
          nodeRepulsion: 8000,
          idealEdgeLength: 120,
          nodeOverlap: 20,
          padding: 30,
          fit: true,
        }).run();
      }
    };
    initCy();
    return () => {
      cyRef.current?.destroy();
      if (cyRef.current) cyRef.current = undefined;
    };
  }, [open, snapshot]);

  // 选中高亮
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass('is-selected is-active-edge');
    if (!selectedId) return;
    const node = cy.getElementById(selectedId);
    if (!node.length) return;
    node.addClass('is-selected');
    node.connectedEdges().addClass('is-active-edge');
  }, [selectedId, snapshot]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-30 flex">
      <div className="ml-auto h-full w-[min(960px,95vw)] bg-gradient-to-b from-gray-900 to-gray-950 border-l border-gray-700/50 flex flex-col shadow-2xl">
        {/* 顶部 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700/50 bg-gray-800/30">
          <div>
            <h3 className="text-sm font-bold text-gray-100">证据攻击地图</h3>
            <p className="text-[10px] text-gray-500 mt-0.5">
              目标 → 假设分支（并行泳道）→ 动作/证据 ·{' '}
              {snapshot ? `v${snapshot.version}` : '加载中'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoRefresh(a => !a)}
              className={`text-[10px] px-2 py-1 rounded border transition-all ${
                autoRefresh
                  ? 'bg-green-600/20 border-green-600/40 text-green-300'
                  : 'bg-gray-800 border-gray-700 text-gray-400'
              }`}
            >
              {autoRefresh ? '● 实时' : '○ 暂停'}
            </button>
            <button
              onClick={() => setShowHintForm(s => !s)}
              className="text-[10px] px-2 py-1 rounded border border-amber-600/40 bg-amber-600/10 text-amber-300 hover:bg-amber-600/20"
            >
              + Hint
            </button>
            <button
              onClick={() => { cyRef.current?.fit(undefined, 40); }}
              className="text-[10px] px-2 py-1 rounded border border-gray-600/40 bg-gray-800 text-gray-300 hover:bg-gray-700"
              title="居中"
            >
              ⊕
            </button>
            <button onClick={onClose} className="p-1 rounded hover:bg-gray-800 text-gray-500">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
        </div>

        {/* 统计条 */}
        {stats && (
          <div className="flex flex-wrap gap-1.5 px-4 py-2 border-b border-gray-700/40 text-[10px] font-medium">
            <span className="px-2 py-0.5 rounded-full bg-orange-600/15 text-orange-300 border border-orange-600/20">⌖ {stats.targets_active ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-teal-600/15 text-teal-300 border border-teal-600/20">❓{stats.hypotheses_pending ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-green-600/15 text-green-300 border border-green-600/20">✅{stats.hypotheses_confirmed ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-red-600/15 text-red-300 border border-red-600/20">❌{stats.hypotheses_refuted ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-blue-600/15 text-blue-300 border border-blue-600/20">≡{stats.evidence ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-blue-600/15 text-blue-200 border border-blue-600/20">✓{stats.facts}</span>
            <span className="px-2 py-0.5 rounded-full bg-indigo-600/15 text-indigo-300 border border-indigo-600/20">▶{stats.actions ?? 0}</span>
            <span className="px-2 py-0.5 rounded-full bg-amber-600/15 text-amber-300 border border-amber-600/20">📋{stats.handoffs ?? 0}</span>
          </div>
        )}

        {showHintForm && (
          <div className="px-4 py-3 border-b border-gray-800 bg-amber-600/5 space-y-2">
            <input
              type="text"
              value={hintLabel}
              onChange={e => setHintLabel(e.target.value)}
              placeholder="提示标题（如：重点关注 AJP 协议）"
              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-amber-500/40"
            />
            <input
              type="text"
              value={hintDetail}
              onChange={e => setHintDetail(e.target.value)}
              placeholder="详细说明（可选）"
              className="w-full px-2 py-1.5 bg-gray-800 border border-gray-700 rounded text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-amber-500/40"
            />
            <div className="flex gap-2">
              <button
                onClick={handleAddHint}
                disabled={!hintLabel.trim()}
                className="flex-1 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium"
              >
                注入
              </button>
              <button
                onClick={() => setShowHintForm(false)}
                className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-400 rounded text-xs"
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 图例 */}
        <div className="flex flex-wrap gap-x-3 gap-y-1 px-4 py-1.5 border-b border-gray-700/40 text-[9px] text-gray-500">
          {Object.entries(KIND_VIS).map(([k, v]) => (
            <span key={k} className="flex items-center gap-1">
              <span style={{ color: v.stroke, fontWeight: 700 }}>{v.icon}</span>
              {v.label}
            </span>
          ))}
        </div>

        {/* 图 */}
        <div className="flex-1 overflow-hidden bg-gray-950/60 relative">
          {loading ? (
            <div className="text-center text-gray-600 py-8 text-xs">加载攻击地图中...</div>
          ) : error ? (
            <div className="text-center py-8">
              <p className="text-xs text-gray-500 mb-2">该会话无黑板</p>
              <p className="text-[10px] text-gray-600">{error}</p>
            </div>
          ) : !snapshot ? (
            <div className="text-center text-gray-600 py-8 text-xs">黑板为空</div>
          ) : (
            <div ref={containerRef} className="w-full h-full" />
          )}
        </div>

        {/* 交接卡 */}
        {handoffs.length > 0 && (
          <div className="px-3 pb-2 max-h-[30%] overflow-y-auto border-t border-gray-700/40">
            <div className="text-[10px] text-amber-300/80 font-bold mb-1.5 mt-2 px-1">
              📋 轮末交接卡 · {handoffs.length} 张 · 跨轮工作记忆
            </div>
            <div className="space-y-1.5">
              {handoffs.map(h => <HandoffCard key={h.id} node={h}
                selected={selectedId === h.id}
                onClick={() => setSelectedId(h.id)} />)}
            </div>
          </div>
        )}

        {/* 节点详情底栏 */}
        {selected && (
          <NodeDetail node={selected} onClose={() => setSelectedId(null)} />
        )}
      </div>
    </div>
  );
}

function HandoffCard({ node, selected, onClick }: {
  node: BBNode; selected: boolean; onClick: () => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const card = (node.properties?.card || {}) as Record<string, any>;
  const rnd = node.properties?.round;
  const vp: string[] = card.verified_paths || [];
  const de: string[] = card.dead_ends || [];
  const ns: string[] = card.next_steps || [];
  const arts: { name?: string }[] = card.artifacts || [];
  const expanded = open || selected;
  return (
    <div
      onClick={onClick}
      className={`rounded-lg border px-3 py-2 text-[10px] cursor-pointer transition-all ${
        selected
          ? 'border-amber-400/70 bg-amber-600/15 shadow-lg'
          : 'border-amber-600/25 bg-amber-600/5 hover:bg-amber-600/10 hover:border-amber-600/40'
      }`}
    >
      <div className="flex items-center justify-between" onClick={e => { e.stopPropagation(); setOpen(o => !o); }}>
        <span className="font-medium text-amber-200">
          📋 第 {rnd ?? '?'} 轮 · {card.summary || node.label}
          {card.target_changed ? <span className="ml-1 text-red-300">🔴目标变更</span> : null}
        </span>
        <span className="text-gray-500">{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && (
        <div className="mt-1 space-y-1 text-gray-300">
          {card.current_target && (
            <div><span className="text-orange-300">目标：</span>{card.current_target}</div>
          )}
          {vp.length > 0 && (
            <div>
              <span className="text-green-300">已走通：</span>
              {vp.map((p, i) => <div key={i} className="pl-3 text-gray-400">· {p}</div>)}
            </div>
          )}
          {de.length > 0 && (
            <div>
              <span className="text-red-300/90">死路：</span>
              {de.map((p, i) => <div key={i} className="pl-3 text-gray-500 opacity-60 line-through">· {p}</div>)}
            </div>
          )}
          {ns.length > 0 && (
            <div>
              <span className="text-teal-300">下一步：</span>
              {ns.map((p, i) => <div key={i} className="pl-3 text-gray-400">→ {p}</div>)}
            </div>
          )}
          {arts.length > 0 && (
            <div className="space-y-1">
              {arts.map((a: any, i: number) => (
                <div key={i}>
                  <div className="text-indigo-300/90">🧩 工件 {a.name}：</div>
                  {a.content ? (
                    <pre className="mt-0.5 text-[9px] text-gray-400 whitespace-pre-wrap break-all font-mono bg-gray-950/60 border border-indigo-900/40 rounded p-1.5 max-h-32 overflow-y-auto">
                      {String(a.content).slice(0, 2000)}
                    </pre>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NodeDetail({ node, onClose }: { node: BBNode; onClose: () => void }): JSX.Element {
  const vis = KIND_VIS[node.kind] || KIND_VIS.fact;
  const card = node.kind === 'handoff' ? (node.properties?.card || {}) as Record<string, any> : null;
  return (
    <div className="border-t border-gray-700/50 bg-gray-900/95 max-h-[42%] overflow-y-auto px-4 py-3 backdrop-blur-sm">
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold" style={{ color: vis.stroke }}>
            {vis.icon} {vis.label}
          </span>
          {node.status && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
              {node.status}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xs">✕</button>
      </div>
      <div className="text-xs text-gray-200 font-medium mb-1">{node.label}</div>
      {node.detail && (
        <pre className="text-[10px] text-gray-400 whitespace-pre-wrap break-all font-sans">
          {node.detail}
        </pre>
      )}
      {card && (
        <div className="mt-1.5 text-[10px] text-gray-400">
          {card.current_target && <div>目标：{card.current_target}</div>}
          {(card.artifacts || []).map((a: any, i: number) => (
            <div key={i} className="mt-1">
              <div className="text-indigo-300">工件 {a.name}：</div>
              <pre className="text-[9px] text-gray-500 whitespace-pre-wrap break-all font-mono bg-gray-800/50 rounded p-1">
                {String(a.content || '').slice(0, 800)}
              </pre>
            </div>
          ))}
        </div>
      )}
      {node.discovered_by && node.discovered_by !== 'user' && (
        <div className="text-[9px] text-gray-600 mt-1.5">by {node.discovered_by}</div>
      )}
    </div>
  );
}
