import { useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, CircleDashed, FlaskConical, Moon, RefreshCw, Sun, XCircle } from 'lucide-react';
import { characterAssets, novaProfile } from '@/assets/characterAssets';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { NovaStar } from '@/components/character/NovaStar';
import { SectionTitle } from '@/components/common/Feedback';
import { PageHeader } from '@/components/common/PageHeader';
import { ConnectionBadge, ModeBadge } from '@/components/common/StatusIndicators';
import { useCapabilities } from '@/hooks/queries';
import { useConnectionState } from '@/hooks/useConnectionState';
import { MockAdapter, type MockScenario } from '@/services/adapters/mockAdapter';
import { RealAdapter } from '@/services/adapters/realAdapter';
import { openApiHasRoute, type RouteKey } from '@/services/adapters/realRoutes';
import { useNovaServices } from '@/services/novaContext';
import { useSettingsStore, type FontSize } from '@/stores/settingsStore';
import type { BackendCapabilities } from '@/types/nova';
import { cn } from '@/utils/cn';
import { formatFullDate } from '@/utils/time';

function Card({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn('nova-panel p-5', className)}>
      <SectionTitle className="mb-3">{title}</SectionTitle>
      {children}
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2">
      <div className="min-w-0">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex gap-3 py-1 text-sm">
      <span className="w-28 shrink-0 text-muted-foreground">{k}</span>
      <span className="min-w-0 flex-1 break-all">{v}</span>
    </div>
  );
}

const CAP_LABELS: [keyof BackendCapabilities, string][] = [
  ['conversationPersistence', '会话持久化'],
  ['renameConversation', '重命名会话'],
  ['deleteConversation', '删除会话'],
  ['stopGeneration', '停止生成'],
  ['memory', '记忆查询'],
  ['memoryHistory', '记忆修改记录'],
  ['channels', '渠道状态'],
  ['modelSwitch', '模型切换'],
  ['personaSettings', '人格参数'],
];

export function SettingsPage() {
  const { adapter, config } = useNovaServices();
  const conn = useConnectionState();
  const caps = useCapabilities();
  const s = useSettingsStore();

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="设置" subtitle="外观、连接诊断与关于 Nova" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto grid w-full max-w-5xl gap-4 px-4 py-5 sm:px-6 lg:grid-cols-2">
          <Card title="外观">
            <Field label="主题" hint="明亮星图 / 夜航深色">
              <div className="flex rounded-lg border bg-surface-sunken p-0.5" role="radiogroup" aria-label="主题">
                {(
                  [
                    ['light', '明亮星图', Sun],
                    ['dark', '夜航深色', Moon],
                  ] as const
                ).map(([key, label, Icon]) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={s.theme === key}
                    onClick={() => s.setTheme(key)}
                    className={cn(
                      'flex items-center gap-1 rounded-md px-2.5 py-1 text-xs',
                      s.theme === key ? 'bg-primary text-primary-foreground shadow-soft' : 'hover:bg-primary-soft',
                    )}
                  >
                    <Icon className="size-3.5" /> {label}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="动画效果" hint="星光、轨道与消息入场动效；系统开启「减少动态效果」时始终关闭">
              <Switch checked={s.motion} onCheckedChange={s.setMotion} aria-label="动画效果" />
            </Field>
            <Field label="字体大小">
              <div className="flex rounded-lg border bg-surface-sunken p-0.5" role="radiogroup" aria-label="字体大小">
                {(
                  [
                    ['sm', '小'],
                    ['md', '标准'],
                    ['lg', '大'],
                  ] as [FontSize, string][]
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={s.fontSize === key}
                    onClick={() => s.setFontSize(key)}
                    className={cn(
                      'rounded-md px-3 py-1 text-xs',
                      s.fontSize === key ? 'bg-primary text-primary-foreground shadow-soft' : 'hover:bg-primary-soft',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </Field>
            <p className="mt-2 text-[0.7rem] text-muted-foreground">偏好仅保存在本浏览器，不包含聊天内容或任何凭据。</p>
          </Card>

          <Card title="后端连接">
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <ModeBadge isMock={conn.isMock} />
              <ConnectionBadge state={conn.state} />
              <Button size="sm" variant="ghost" onClick={() => conn.recheck()} disabled={conn.isChecking} className="ml-auto">
                <RefreshCw className={conn.isChecking ? 'animate-spin' : undefined} /> 重新检测
              </Button>
            </div>
            <KV k="API 模式" v={config.mode === 'mock' ? 'mock（演示数据）' : 'real（真实后端）'} />
            <KV k="API 地址" v={<code className="text-xs">{config.apiBaseUrl}</code>} />
            <KV k="最近检测" v={conn.health ? formatFullDate(conn.health.checkedAt) : '—'} />
            <KV k="延迟" v={conn.health?.latencyMs !== undefined ? `${conn.health.latencyMs} ms` : '—'} />
            <KV k="后端版本" v={conn.health?.backendVersion ?? '后端未报告'} />
            <KV k="模型" v={conn.health?.modelName ?? '后端未报告'} />
            {conn.health?.detail && <KV k="说明" v={conn.health.detail} />}
            <KV k="流式配置" v={<code className="text-xs">VITE_NOVA_STREAM_PROTOCOL={config.streamProtocol}</code>} />
            <KV
              k="实际回复方式"
              v={
                caps.data
                  ? caps.data.streaming === 'none'
                    ? '普通响应（非流式）'
                    : `${caps.data.streaming.toUpperCase()} 流式`
                  : '未知（后端未连接）'
              }
            />
            <KV
              k="能力来源"
              v={
                caps.data
                  ? { backend: '后端声明', assumed: '按 OpenAPI / 路由配置推断（未经后端声明）', mock: 'Mock 演示' }[caps.data.source]
                  : '—'
              }
            />
            {config.warnings.length > 0 && (
              <ul className="mt-2 space-y-1 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs">
                {config.warnings.map((w) => (
                  <li key={w}>⚠ {w}</li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="后端能力" className="lg:col-span-2">
            {!caps.data ? (
              <p className="text-sm text-muted-foreground">后端未连接，无法确认能力。未确认的功能在界面中显示为「暂不可用」。</p>
            ) : (
              <ul className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
                {CAP_LABELS.map(([key, label]) => (
                  <li key={key} className="flex items-center gap-2 text-sm">
                    {caps.data[key] ? (
                      <CheckCircle2 className="size-4 text-success" />
                    ) : (
                      <XCircle className="size-4 text-muted-foreground" />
                    )}
                    {label}
                    <span className="text-xs text-muted-foreground">{caps.data[key] ? '可用' : '暂不可用'}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {adapter instanceof MockAdapter && <MockScenarioCard adapter={adapter} />}
          {adapter instanceof RealAdapter && <RouteDiagnostics adapter={adapter} />}

          <Card title="关于 Nova" className="lg:col-span-2">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
              <NovaStar size={56} orbit animated />
              <div className="min-w-0 flex-1 space-y-1 text-sm">
                <p>
                  <span className="nova-gradient-text font-display text-xl font-semibold italic">Nova</span>{' '}
                  <span className="text-muted-foreground">V3.0 · {novaProfile.subtitle}</span>
                </p>
                <p>{novaProfile.identity}。{conn.isMock ? novaProfile.personality : 'AI 角色，温柔、好奇、略带俏皮，有独立观点'}。</p>
                {conn.isMock && <p className="text-muted-foreground">演示设定中的物品：{novaProfile.items.join('、')}</p>}
                <KV k="前端版本" v={`Nova UI-01 v${config.appVersion}`} />
                <KV
                  k="角色素材"
                  v={
                    characterAssets.avatar.provisional ? (
                      <span>
                        <Badge tone="warning">临时素材</Badge> 当前头像 / 立绘裁切自设定图，建议替换为高清透明素材（见 src/assets/characterAssets.ts）
                      </span>
                    ) : (
                      '正式素材'
                    )
                  }
                />
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function MockScenarioCard({ adapter }: { adapter: MockAdapter }) {
  const qc = useQueryClient();
  const [scenario, setScenario] = useState<MockScenario>({ ...adapter.scenario });
  const set = (patch: Partial<MockScenario>) => {
    Object.assign(adapter.scenario, patch);
    setScenario({ ...adapter.scenario });
    void qc.invalidateQueries();
  };
  return (
    <Card title="Mock 演示场景" className="lg:col-span-2">
      <p className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <FlaskConical className="size-3.5" /> 仅演示模式可用，用于检查各种异常状态下的界面表现。
      </p>
      <div className="grid gap-x-8 sm:grid-cols-3">
        <Field label="模拟后端离线">
          <Switch checked={scenario.offline} onCheckedChange={(v) => set({ offline: v })} aria-label="模拟后端离线" />
        </Field>
        <Field label="下一条消息失败">
          <Switch
            checked={scenario.failNextMessage}
            onCheckedChange={(v) => set({ failNextMessage: v })}
            aria-label="下一条消息失败"
          />
        </Field>
        <Field label="模拟流式回复">
          <Switch checked={scenario.streaming} onCheckedChange={(v) => set({ streaming: v })} aria-label="模拟流式回复" />
        </Field>
      </div>
    </Card>
  );
}

const ROUTE_LABEL: Record<RouteKey, string> = {
  health: '健康检查',
  models: '可用模型',
  capabilities: '能力声明',
  openapi: 'OpenAPI 文档',
  listConversations: '会话列表',
  createConversation: '创建会话',
  renameConversation: '重命名会话',
  deleteConversation: '删除会话',
  conversationMessages: '会话消息',
  sendMessage: '发送消息',
  streamMessage: '流式发送',
  stopGeneration: '停止生成',
  memoryRecords: '记忆列表',
  channelStatuses: '渠道状态',
};

/** 真实模式：显示路由映射，并与后端 OpenAPI 文档逐项比对，方便联调 */
function RouteDiagnostics({ adapter }: { adapter: RealAdapter }) {
  const routes = adapter.getRouteMap();
  const openapi = useQuery({
    queryKey: ['nova', 'openapi-doc'],
    queryFn: ({ signal }) => adapter.fetchOpenApi(signal),
    retry: false,
  });
  return (
    <Card title="接口映射诊断" className="lg:col-span-2">
      <p className="mb-3 text-xs text-muted-foreground">
        路由定义在 <code>src/services/adapters/realRoutes.ts</code>。「建议」表示尚未与真实后端核对。
        {openapi.data ? ' 已读取后端 OpenAPI 文档并逐项比对。' : openapi.isPending ? ' 正在读取 OpenAPI 文档…' : ' 未能读取 OpenAPI 文档，无法自动比对。'}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted-foreground">
            <tr className="border-b">
              <th className="py-1.5 pr-3 font-medium">能力</th>
              <th className="py-1.5 pr-3 font-medium">方法</th>
              <th className="py-1.5 pr-3 font-medium">路径</th>
              <th className="py-1.5 pr-3 font-medium">核实状态</th>
              <th className="py-1.5 font-medium">OpenAPI 中</th>
            </tr>
          </thead>
          <tbody>
            {(Object.keys(ROUTE_LABEL) as RouteKey[]).map((key) => {
              const spec = routes[key];
              const found = spec && openapi.data ? openApiHasRoute(openapi.data, spec, '/api') : undefined;
              return (
                <tr key={key} className="border-b last:border-0">
                  <td className="py-1.5 pr-3">{ROUTE_LABEL[key]}</td>
                  <td className="py-1.5 pr-3 font-mono">{spec?.method ?? '—'}</td>
                  <td className="py-1.5 pr-3 font-mono break-all">{spec?.path ?? '未配置'}</td>
                  <td className="py-1.5 pr-3">
                    {spec ? (
                      <Badge tone={spec.status === 'verified' ? 'success' : 'warning'}>{spec.status === 'verified' ? '已核实' : '建议'}</Badge>
                    ) : (
                      <Badge>未实现</Badge>
                    )}
                  </td>
                  <td className="py-1.5">
                    {found === undefined ? (
                      <CircleDashed className="size-4 text-muted-foreground" aria-label="无法比对" />
                    ) : found ? (
                      <CheckCircle2 className="size-4 text-success" aria-label="存在" />
                    ) : (
                      <XCircle className="size-4 text-danger" aria-label="不存在" />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
