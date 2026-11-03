import { NovaApiError } from '@/services/api/errors';
import type { NovaAdapter } from '@/services/adapters/types';
import type { ChannelInfo, ChannelType } from '@/types/nova';

/**
 * 渠道管理服务接口（Web / QQ Bot / 微信 Bot）。
 *
 * 前端只负责「展示状态 + 发起管理请求」。以下逻辑必须由 Nova 后端实现，前端不得持有：
 * 平台身份映射、Token / 密钥、回调验证、账号授权、消息收发、去重、限频。
 * 前端不直接驱动 QQ / 微信客户端，不在浏览器持久化任何平台凭据。
 */
export interface ChannelLogEntry {
  at: string;
  level: 'info' | 'warn' | 'error';
  message: string;
}

export interface ChannelService {
  getChannelStatuses(signal?: AbortSignal): Promise<ChannelInfo[]>;
  /** 跳转后端提供的配置流程（预留） */
  getConfigEntry(channelType: ChannelType): Promise<{ url: string }>;
  /** 读取渠道连接日志（预留） */
  getChannelLogs(channelId: string, signal?: AbortSignal): Promise<ChannelLogEntry[]>;
}

export class AdapterChannelService implements ChannelService {
  private readonly adapter: NovaAdapter;
  constructor(adapter: NovaAdapter) {
    this.adapter = adapter;
  }

  getChannelStatuses(signal?: AbortSignal) {
    return this.adapter.getChannelStatuses({ signal });
  }

  async getConfigEntry(channelType: ChannelType): Promise<{ url: string }> {
    throw new NovaApiError('not_implemented', `${CHANNEL_META[channelType].name} 的配置流程等待后端提供`);
  }

  async getChannelLogs(_channelId: string): Promise<ChannelLogEntry[]> {
    throw new NovaApiError('not_implemented', '渠道日志接口等待后端提供');
  }
}

/** 渠道静态说明。仅描述「平台可能的能力与限制」，不代表已接入。 */
export const CHANNEL_META: Record<
  ChannelType,
  { name: string; description: string; abilities: string[]; caveats: string[] }
> = {
  web: {
    name: 'Web 终端',
    description: '当前这个浏览器界面，直接与 Nova 后端通信。',
    abilities: ['在浏览器中收发消息', '支持流式回复（取决于后端）', '查看夜航记录与星图档案'],
    caveats: ['不能在页面关闭后主动推送消息'],
  },
  qq: {
    name: 'QQ Bot',
    description: '通过 QQ 官方机器人开放平台接入，由后端负责鉴权与消息收发。',
    abilities: ['接收用户发给机器人的消息（后端接入后）', '在平台规则允许范围内回复'],
    caveats: [
      '主动消息受平台频率与场景限制，不保证无限制推送',
      'AppID / Token / Secret 只能保存在后端',
      '需要后端完成回调验证、身份映射、去重与限频',
    ],
  },
  wechat: {
    name: '微信 Bot',
    description: '可能对应公众号、企业微信或其它产品，开放能力各不相同，需后端确认接入方式。',
    abilities: ['接收与回复消息（取决于所选微信产品与后端实现）'],
    caveats: [
      '个人微信号没有可供自动控制的官方 API',
      '公众号 / 企业微信的主动消息规则不同，需按平台政策处理',
      '密钥与回调验证必须由后端处理',
    ],
  },
};
