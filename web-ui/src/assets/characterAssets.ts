/**
 * Nova 角色素材槽位。
 *
 * 当前素材是从角色设定图中裁切出的「临时素材」（带背景、分辨率有限），
 * 仅用于开发与演示。拿到高清透明素材后，把文件放进 public/nova/ 并修改这里的路径即可，
 * 组件无需改动。任何素材加载失败时，组件会自动回退到 SVG 四芒星徽记，不会出现破图。
 *
 * 建议规格：
 *   avatar    正方形，≥ 512×512，PNG/WebP，人物面部居中
 *   portrait  竖版半身立绘，≥ 900×1200，透明背景 PNG/WebP
 *   expressions 各表情头像，≥ 256×256
 */
export interface CharacterAssetSlot {
  src: string | null;
  /** 是否为临时裁切素材（会在设置页提示替换） */
  provisional: boolean;
  alt: string;
}

export const characterAssets = {
  avatar: { src: '/nova/avatar.webp', provisional: true, alt: 'Nova 头像' },
  portrait: { src: '/nova/portrait.webp', provisional: true, alt: 'Nova 半身立绘' },
  expressions: {
    happy: { src: '/nova/expr-happy.webp', provisional: true, alt: 'Nova 开心的表情' },
    curious: { src: '/nova/expr-curious.webp', provisional: true, alt: 'Nova 疑惑的表情' },
    sleepy: { src: '/nova/expr-sleepy.webp', provisional: true, alt: 'Nova 抱着玩偶休息' },
    gentle: { src: '/nova/expr-gentle.webp', provisional: true, alt: 'Nova 温柔的表情' },
  },
} satisfies {
  avatar: CharacterAssetSlot;
  portrait: CharacterAssetSlot;
  expressions: Record<string, CharacterAssetSlot>;
};

export type ExpressionName = keyof typeof characterAssets.expressions;

/** 角色身份小档案（来自设定图，可按后端人格配置替换） */
export const novaProfile = {
  name: 'Nova',
  subtitle: 'Evolutionary Companion Agent',
  identity: '星图档案馆的夜航记录员',
  personality: '温柔、好奇、开朗，偶尔带一点俏皮；热爱学习与探索，喜欢把新发现整理成档案',
  interests: ['阅读', '整理星图档案', '观星', '记录奇妙的故事与世界的变化'],
  greeting: '你好，我是 Nova ✧ 一起阅读、记录，并继续探索这个还在扩展的世界吧？♡',
  items: ['星图档案册', '星轨仪', '记录墨水', '星图卷轴'],
};
