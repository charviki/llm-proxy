"""SSL 证书生成器"""
from pathlib import Path
import subprocess
import tempfile
import shutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class CertGenerator:
    """SSL 证书生成器"""
    ca_dir: Path = Path("ca")

    def generate(self, domains: Optional[list[str]] = None) -> None:
        """生成 CA 证书和服务器证书"""
        if domains is None:
            domains = ["api.openai.com"]

        self.ca_dir.mkdir(parents=True, exist_ok=True)
        self._create_config_files(domains)
        self._generate_ca_cert()
        self._generate_server_cert(domains)
        print("\n✨ " + "="*46)
        print("✨ 证书处理流程全部完成！")
        print("✨ " + "="*46 + "\n")

    def _run_command(self, command: str, check: bool = True) -> subprocess.CompletedProcess:
        print(f"  ➜ 执行命令: {command}")
        result = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"命令执行失败: {command}\n{result.stderr}")
        return result

    def _create_config_files(self, domains: list[str]) -> None:
        """创建配置文件"""
        openssl_cnf = """[ req ]
default_bits        = 2048
default_md          = sha256
default_keyfile     = privkey.pem
distinguished_name  = req_distinguished_name
req_extensions      = v3_req
x509_extensions     = v3_ca

[ req_distinguished_name ]
countryName                     = 国家代码 (2字符)
countryName_default             = CN
stateOrProvinceName             = 省/州
stateOrProvinceName_default     = State
localityName                    = 城市
localityName_default            = City
organizationName                = 组织名称
organizationName_default        = Organization
organizationalUnitName          = 组织单位名称
organizationalUnitName_default  = Unit
commonName                      = 通用名称
commonName_max                  = 64
commonName_default              = localhost
emailAddress                    = 电子邮件地址
emailAddress_max                = 64
emailAddress_default            = admin@example.com

[ v3_req ]
basicConstraints       = CA:FALSE
keyUsage               = nonRepudiation, digitalSignature, keyEncipherment
extendedKeyUsage       = serverAuth
subjectAltName         = @alt_names

[ v3_ca ]
basicConstraints       = critical, CA:true
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid:always, issuer:always
keyUsage               = cRLSign, keyCertSign, digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
"""
        v3_req_cnf = """[ v3_req ]
basicConstraints       = CA:FALSE
keyUsage               = nonRepudiation, digitalSignature, keyEncipherment
extendedKeyUsage       = serverAuth
subjectAltName         = @alt_names
"""
        with open(self.ca_dir / "openssl.cnf", "w") as f:
            f.write(openssl_cnf)

        with open(self.ca_dir / "v3_req.cnf", "w") as f:
            f.write(v3_req_cnf)

        domain_cnf = "[ alt_names ]\n"
        for i, domain in enumerate(domains, 1):
            domain_cnf += f"DNS.{i} = {domain}\n"

        with open(self.ca_dir / "llm-proxy.cnf", "w") as f:
            f.write(domain_cnf)

        with open(self.ca_dir / "llm-proxy.subj", "w") as f:
            f.write("/C=CN/ST=State/L=City/O=Organization/OU=Unit/CN=LLM-Proxy")

    def _generate_ca_cert(self) -> None:
        """生成 CA 证书"""
        ca_key = self.ca_dir / "llm-proxy-ca.key"
        ca_crt = self.ca_dir / "llm-proxy-ca.crt"

        if ca_crt.exists():
            print("🟢 [CA证书] 已存在，跳过生成")
            return

        print("🟡 [CA证书] 未找到，正在生成 CA 证书...")
        self._run_command(f"openssl genrsa -out {ca_key} 2048")
        self._run_command(
            f'openssl req -new -x509 -days 36500 -key {ca_key} -out {ca_crt} '
            f'-subj "/C=CN/ST=State/L=City/O=LLM-Proxy CA/OU=LLM-Proxy/CN=LLM-Proxy Root CA"'
        )
        print("✅ [CA证书] 生成完成")

    def _generate_server_cert(self, domains: list[str]) -> None:
        """生成服务器证书"""
        crt_file = self.ca_dir / "llm-proxy.crt"
        key_file = self.ca_dir / "llm-proxy.key"
        
        # 验证是否需要重新生成
        if crt_file.exists() and key_file.exists():
            import subprocess
            try:
                # 使用 openssl 命令直接检查证书中包含的域名
                result = subprocess.run(
                    ["openssl", "x509", "-in", str(crt_file), "-text", "-noout"],
                    capture_output=True, text=True, check=True
                )
                output = result.stdout
                
                # 检查所有请求的域名是否都在证书中
                all_domains_exist = True
                for domain in domains:
                    if f"DNS:{domain}" not in output:
                        all_domains_exist = False
                        break
                        
                if all_domains_exist:
                    print(f"🟢 [服务器证书] 已存在且包含所有请求的域名，跳过生成")
                    return
            except FileNotFoundError:
                print(f"\n❌ [错误] 未找到 openssl 命令。")
                print("请确保已安装 OpenSSL 并将其添加到了系统环境变量中。")
                print("  - macOS: brew install openssl")
                print("  - Ubuntu/Debian: sudo apt install openssl")
                print("  - Windows: 请通过 Scoop (scoop install openssl) 或官网下载安装。")
                import sys
                sys.exit(1)
            except Exception as e:
                print(f"🟡 [服务器证书] 验证现有证书失败: {e}，将重新生成")

        print(f"🟡 [服务器证书] 需要更新或不存在，正在为域名 {', '.join(domains)} 重新生成服务器证书...")

        with open(self.ca_dir / "openssl.cnf", "r") as f:
            openssl_cnf = f.read()
        with open(self.ca_dir / "v3_req.cnf", "r") as f:
            v3_req_cnf = f.read()
        with open(self.ca_dir / "llm-proxy.cnf", "r") as f:
            domain_cnf = f.read()
        with open(self.ca_dir / "llm-proxy.subj", "r") as f:
            domain_subj = f.read().strip()

        merged_cnf = openssl_cnf + "\n" + v3_req_cnf + "\n" + domain_cnf
        temp_cnf_path = self._create_temp_file(merged_cnf)

        try:
            key_file = self.ca_dir / "llm-proxy.key"
            csr_file = self.ca_dir / "llm-proxy.csr"
            crt_file = self.ca_dir / "llm-proxy.crt"

            self._run_command(f"openssl genrsa -out {key_file} 2048")
            self._run_command(f"openssl pkcs8 -topk8 -nocrypt -in {key_file} -out {key_file}.pkcs8")
            shutil.move(str(key_file) + ".pkcs8", key_file)

            self._run_command(
                f'openssl req -reqexts v3_req -sha256 -new -key {key_file} '
                f'-out {csr_file} -config {temp_cnf_path} -subj "{domain_subj}"'
            )

            self._run_command(
                f'openssl x509 -req -days 365 -in {csr_file} '
                f'-CA {self.ca_dir / "llm-proxy-ca.crt"} '
                f'-CAkey {self.ca_dir / "llm-proxy-ca.key"} '
                f'-CAcreateserial -out {crt_file} '
                f'-extfile {temp_cnf_path} -extensions v3_req'
            )

            if csr_file.exists():
                csr_file.unlink()

            print(f"✅ [服务器证书] 生成完成: {crt_file}")
            if len(domains) > 1:
                print(f"   包含以下 SAN: {', '.join(domains)}")

        finally:
            if temp_cnf_path.exists():
                temp_cnf_path.unlink()

    def _create_temp_file(self, content: str) -> Path:
        """创建临时配置文件"""
        fd, path = tempfile.mkstemp()
        Path(path).write_text(content, encoding='utf-8')
        return Path(path)
