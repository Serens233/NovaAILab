import type { Conversation, NovaMessage } from '@/types/nova';

/**
 * 仅存在于内存中的临时会话仓库。
 * 用途：真实后端没有会话持久化接口时，仍然可以和后端真实对话，但会话只在当前页面有效。
 * 这里不包含任何模拟回复 —— 只保存真实后端返回的消息。
 */
export class EphemeralConversationStore {
  private conversations = new Map<string, Conversation>();
  private messages = new Map<string, NovaMessage[]>();
  private counter = 0;

  static isLocalId(id: string): boolean {
    return id.startsWith('local-');
  }

  list(): Conversation[] {
    return [...this.conversations.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }

  create(title?: string): Conversation {
    this.counter += 1;
    const now = new Date().toISOString();
    const conv: Conversation = {
      conversationId: `local-${Date.now().toString(36)}-${this.counter}`,
      title: title?.trim() || '临时夜航记录',
      createdAt: now,
      updatedAt: now,
      channelType: 'web',
      localOnly: true,
      messageCount: 0,
    };
    this.conversations.set(conv.conversationId, conv);
    this.messages.set(conv.conversationId, []);
    return conv;
  }

  rename(id: string, title: string): Conversation | undefined {
    const conv = this.conversations.get(id);
    if (!conv) return undefined;
    const next = { ...conv, title, updatedAt: new Date().toISOString() };
    this.conversations.set(id, next);
    return next;
  }

  delete(id: string): void {
    this.conversations.delete(id);
    this.messages.delete(id);
  }

  getMessages(id: string): NovaMessage[] | undefined {
    const list = this.messages.get(id);
    return list ? [...list] : undefined;
  }

  append(id: string, ...msgs: NovaMessage[]): void {
    const list = this.messages.get(id);
    const conv = this.conversations.get(id);
    if (!list || !conv) return;
    list.push(...msgs);
    const last = msgs[msgs.length - 1];
    this.conversations.set(id, {
      ...conv,
      updatedAt: new Date().toISOString(),
      messageCount: list.length,
      preview: last?.content.slice(0, 60),
    });
  }
}
