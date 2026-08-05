"""Beacon CLI — 本地命令行工具(STEP→DXF), 不依赖SaaS

用法:
  python -m engine.zwcad_render convert input.stp --output out.dxf --engine zwcad
  python -m engine.zwcad_render convert input.stp --output out.dxf --engine freecad
  python -m engine.zwcad_render test-zwcad  # 测试ZWCAD COM连接
"""
import argparse
import asyncio
import os
import sys
import logging

# 添加项目根目录到path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def cmd_convert(args):
    """转换STEP→DXF"""
    stp_path = args.stp
    output = args.output or os.path.splitext(stp_path)[0] + '.dxf'
    engine = args.engine
    output_dir = os.path.dirname(os.path.abspath(output))

    logger.info(f"Converting: {stp_path} → {output} (engine: {engine})")

    if engine == 'zwcad':
        from engine.zwcad_adapter import run_zwcad_render
        result = asyncio.run(run_zwcad_render(
            stp_path=stp_path,
            output_dir=output_dir,
        ))
        if result['success']:
            logger.info(f"✅ ZWCAD渲染成功: {result['dxf_path']}")
        else:
            logger.error(f"❌ ZWCAD渲染失败: {result['error']}")
            sys.exit(1)

    elif engine == 'freecad':
        # 调用现有FreeCAD管线(修复后)
        from engine.freecad_adapter import run_freecad
        # TODO: 接入修复后的FreeCAD管线
        logger.error("FreeCAD引擎暂未接入CLI, 请用 --engine zwcad")
        sys.exit(1)

    else:
        logger.error(f"未知引擎: {engine}")
        sys.exit(1)


def cmd_test_zwcad(args):
    """测试ZWCAD COM连接"""
    from engine.zwcad_adapter import test_zwcad_connection
    host = args.host or 'frp-c2061'
    if test_zwcad_connection(host):
        print(f"✅ ZWCAD COM连接成功 (host: {host})")
    else:
        print(f"❌ ZWCAD COM连接失败 (host: {host})")
        print("请检查:")
        print(f"  1. SSH到S5可用: ssh {host}")
        print("  2. S5上ZWCAD已安装且pywin32已装")
        print("  3. ZWCAD COM注册正常")


def main():
    parser = argparse.ArgumentParser(
        description='Beacon STP→DXF 转换工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # ZWCAD引擎转换(STEP→DXF)
  python -m engine.zwcad_render convert part.stp -o output.dxf --engine zwcad

  # 测试ZWCAD COM连接
  python -m engine.zwcad_render test-zwcad --host frp-c2061
        """
    )
    sub = parser.add_subparsers(dest='command')

    # convert子命令
    p_conv = sub.add_parser('convert', help='STEP→DXF转换')
    p_conv.add_argument('stp', help='STEP文件路径')
    p_conv.add_argument('-o', '--output', help='输出DXF路径')
    p_conv.add_argument('--engine', choices=['zwcad', 'freecad'], default='zwcad',
                        help='渲染引擎(默认zwcad)')
    p_conv.set_defaults(func=cmd_convert)

    # test-zwcad子命令
    p_test = sub.add_parser('test-zwcad', help='测试ZWCAD COM连接')
    p_test.add_argument('--host', default='frp-c2061', help='SSH主机别名')
    p_test.set_defaults(func=cmd_test_zwcad)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
