"""CLI 入口 — python -m src.main <command> <args>

命令：
  bot        启动飞书 Bot（长连接模式）
  search     搜索知识库
  recent     查看最近入库
  stats      知识库统计
"""
import argparse
import logging
import sys

from .knowledge_store import KnowledgeStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("omnivault")

store = KnowledgeStore()


def cmd_bot(args):
    from .feishu_bot import main as bot_main
    return bot_main()


def cmd_search(args):
    results = store.search(args.query, args.limit)
    if not results:
        print(f"未找到与「{args.query}」相关的内容。")
        return 0
    print(f"\n搜索「{args.query}」— {len(results)} 条结果:\n")
    for r in results:
        print(f"  [{r['id']}] {r['title'][:50]}")
        print(f"       作者: {r['author']}  |  标签: {r['tags'][:60]}")
        print(f"       摘要: {r.get('snippet', '')[:100]}")
        print()
    return 0


def cmd_recent(args):
    results = store.list_recent(args.limit)
    if not results:
        print("知识库为空。")
        return 0
    print(f"\n最近 {len(results)} 条:\n")
    for r in results:
        print(f"  [{r['id']}] {r['title'][:50]}")
        print(f"       作者: {r['author']}  |  标签: {r['tags'][:60]}")
        print(f"       时间: {r['created_at'][:19]}")
        print()
    return 0


def cmd_stats(args):
    s = store.stats()
    print(f"总条目: {s['total_entries']}")
    print(f"最新: {s['latest_entry']}")
    print(f"数据库: {s['db_path']}")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="omnivault", description="全平台知识库")
    sub = parser.add_subparsers(dest="command", help="子命令")

    p = sub.add_parser("bot", help="启动飞书 Bot")
    p.set_defaults(func=cmd_bot)

    p = sub.add_parser("search", help="搜索知识库")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("--limit", "-n", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("recent", help="查看最近入库")
    p.add_argument("--limit", "-n", type=int, default=10)
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser("stats", help="知识库统计")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
