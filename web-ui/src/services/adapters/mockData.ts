import type { ChannelInfo, Conversation, MemoryRecord, NovaMessage } from '@/types/nova';

/**
 * Mock 演示数据 —— 全部为虚构内容，仅在 VITE_NOVA_API_MODE=mock 时使用。
 * 固定时间基准，保证测试与演示可重现。
 */
export const MOCK_EPOCH = Date.parse('2026-10-28T13:00:00.000Z');
const t = (minutes: number) => new Date(MOCK_EPOCH + minutes * 60_000).toISOString();

export function seedConversations(): Conversation[] {
  return [
    {
      conversationId: 'mock-conv-orion',
      title: '猎户座的观星笔记',
      createdAt: t(0),
      updatedAt: t(42),
      channelType: 'web',
      messageCount: 4,
      preview: '参宿四和参宿七，一红一蓝……',
    },
    {
      conversationId: 'mock-conv-first',
      title: '初次见面的夜航记录',
      createdAt: t(-1440),
      updatedAt: t(-1400),
      channelType: 'web',
      messageCount: 2,
      preview: '你好，我是 Nova ✧',
    },
  ];
}

export function seedMessages(): Record<string, NovaMessage[]> {
  const m = (conversationId: string, id: string, role: NovaMessage['role'], content: string, minute: number): NovaMessage => ({
    conversationId,
    messageId: id,
    role,
    content,
    timestamp: t(minute),
    channelType: 'web',
    deliveryStatus: role === 'assistant' ? 'mock' : undefined,
  });
  return {
    'mock-conv-orion': [
      m('mock-conv-orion', 'm1', 'user', '今晚能看到猎户座吗？', 30),
      m(
        'mock-conv-orion',
        'm2',
        'assistant',
        '可以哦 ✧ 入冬之后，猎户座会在晚上八九点从东南方升起。\n\n- **参宿四**：右肩上偏红的那颗\n- **参宿七**：左脚边蓝白色的亮星\n- 中间三颗排成一列的，就是**猎户腰带**\n\n要不要我把今晚的观测条件也记进档案里？',
        31,
      ),
      m('mock-conv-orion', 'm3', 'user', '好呀，帮我记一下。', 41),
      m('mock-conv-orion', 'm4', 'assistant', '已经夹进「观星笔记」那一册了 📖 下次我们可以对照着星图一起找。', 42),
    ],
    'mock-conv-first': [
      m('mock-conv-first', 'f1', 'user', '你好！', -1441),
      m('mock-conv-first', 'f2', 'assistant', '你好，我是 Nova ✧\n一起阅读、记录，并继续探索这个还在扩展的世界吧？♡', -1400),
    ],
  };
}

export function seedMemories(): MemoryRecord[] {
  return [
    {
      memoryId: 'mem-1',
      title: '喜欢在冬夜观星',
      content: '用户提到冬天晚上最喜欢看猎户座，尤其在意参宿四的颜色。',
      category: 'preference',
      createdAt: t(42),
      importance: 4,
      source: { channelType: 'web', conversationId: 'mock-conv-orion', description: '夜航记录：猎户座的观星笔记' },
      revisions: [
        { revisedAt: t(42), summary: '首次记录' },
        { revisedAt: t(60), summary: '补充：在意参宿四的颜色' },
      ],
    },
    {
      memoryId: 'mem-2',
      title: '第一次见面',
      content: '与用户第一次在 Web 终端相遇，互相打了招呼。',
      category: 'event',
      createdAt: t(-1400),
      importance: 5,
      source: { channelType: 'web', conversationId: 'mock-conv-first', description: '夜航记录：初次见面' },
      revisions: [{ revisedAt: t(-1400), summary: '首次记录' }],
    },
    {
      memoryId: 'mem-3',
      title: '猎户座的亮星',
      content: '参宿四（红超巨星）与参宿七（蓝超巨星）是猎户座最亮的两颗星。',
      category: 'knowledge',
      createdAt: t(31),
      importance: 3,
      source: { channelType: 'web', description: 'Nova 整理的星图知识' },
    },
    {
      memoryId: 'mem-4',
      title: '睡前聊天的习惯',
      content: '用户通常在晚上十一点前后来聊天，聊完会道晚安。',
      category: 'habit',
      createdAt: t(-600),
      importance: 2,
      source: { description: '多次对话归纳' },
    },
    {
      memoryId: 'mem-5',
      title: '约定一起整理星图',
      content: '和用户约好，之后把每次观星的记录都整理进星图档案馆。',
      category: 'relationship',
      createdAt: t(43),
      importance: 4,
      source: { channelType: 'web', conversationId: 'mock-conv-orion' },
    },
  ];
}

export function seedChannels(): ChannelInfo[] {
  return [
    {
      channelId: 'web',
      channelType: 'web',
      displayName: 'Web 终端',
      status: 'connected',
      capabilities: ['receive', 'send', 'stream'],
      lastSyncAt: t(42),
      proactiveMessaging: 'unknown',
    },
    {
      channelId: 'qq',
      channelType: 'qq',
      displayName: 'QQ Bot',
      status: 'not_configured',
      capabilities: [],
      proactiveMessaging: 'unknown',
    },
    {
      channelId: 'wechat',
      channelType: 'wechat',
      displayName: '微信 Bot',
      status: 'not_configured',
      capabilities: [],
      proactiveMessaging: 'unknown',
    },
  ];
}

/** 依据关键词选择可重现的演示回复 */
export function mockReplyFor(input: string): string {
  const text = input.trim();
  if (/你好|您好|hello|hi\b|嗨/i.test(text)) {
    return '你好呀 ✧ 我是 Nova，星图档案馆的夜航记录员。\n\n今晚想一起读点什么、记点什么，还是去看看星星？';
  }
  if (/代码|code|程序|脚本/i.test(text)) {
    return '这是一段演示用的代码块，可以点击右上角复制：\n\n```ts\nfunction nextStar(stars: string[]) {\n  return stars[Math.floor(Math.random() * stars.length)];\n}\n```\n\n（演示模式下的回复是预设内容，并不是真实的模型输出哦。）';
  }
  if (/记忆|档案|记得/.test(text)) {
    return '记忆会被整理进**星图档案馆**：\n\n1. 每条记忆都有形成时间\n2. 会标记来自哪段夜航记录\n3. 修改过的地方也会留下记录\n\n在左侧点「星图档案」就能翻阅了 📖';
  }
  if (/星|宇宙|月亮|天文/.test(text)) {
    return '说到星星我就停不下来啦 ✧\n\n> 每一颗星的光，都是很久以前出发的信。\n\n要把这个话题记进今晚的观星笔记吗？';
  }
  const quote = text.length > 40 ? `${text.slice(0, 40)}…` : text;
  return `我把「${quote}」记在今晚的夜航记录里了 ✧\n\n（这是 Mock 演示模式的预设回复，用于检查界面；连接真实后端后会由 Nova 本体回答。）`;
}
