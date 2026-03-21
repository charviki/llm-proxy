#!/usr/bin/env python3
"""LLM-Proxy CLI 工具"""
import argparse
import os
import sys
from cert import CertGenerator


def main():
    parser = argparse.ArgumentParser(description='LLM-Proxy CLI')
    subparsers = parser.add_subparsers(dest='command', help='子命令')

    cert_parser = subparsers.add_parser('cert', help='生成证书')
    cert_parser.add_argument('--domain', action='append',
        help='域名（可多次指定）')

    args = parser.parse_args()

    if args.command == 'cert':
        domains = args.domain
        
        if not domains:
            try:
                import yaml
                config_path = os.environ.get('CONFIG_PATH', 'config.yml')
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    domains = config.get('server', {}).get('domains', [])
            except Exception:
                pass
                
        # 保底域名
        if not domains:
            domains = ['api.openai.com']
        
        print("\n" + "="*50)
        print(f"🔒 证书检查/生成任务启动")
        print(f"🎯 目标域名: {', '.join(domains)}")
        print("="*50 + "\n")
        
        try:
            CertGenerator().generate(domains=domains)
        except Exception as e:
            print(f"\n❌ [错误] 证书处理失败: {e}")
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
