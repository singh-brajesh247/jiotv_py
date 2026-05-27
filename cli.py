"""Command-line entrypoint for the Python application."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from . import constants, epg, secure_url, server, store
from .config import cfg
from .scheduler import scheduler
from .utils import get_path_prefix, init_logger, login_send_otp, login_verify_otp, logout


PID_FILE_NAME = ".JIO_tv.pid"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        args.host = "localhost"
        args.port = "5001"
        args.public = False
        args.tls = False
        args.tls_cert = ""
        args.tls_key = ""
        args.handler = serve_command
    return args.handler(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jio_py",
        description="Stream JioTV on any device",
    )
    parser.add_argument("-c", "--config", default="", help="Path to config file")
    parser.add_argument(
        "--skip-update-check",
        action="store_true",
        help="Accepted for CLI compatibility",
    )
    parser.add_argument("--version", action="version", version=constants.VERSION)

    subcommands = parser.add_subparsers(dest="command")
    serve_parser = subcommands.add_parser("serve", aliases=["run", "start"])
    serve_parser.add_argument("-H", "--host", default="localhost")
    serve_parser.add_argument("-p", "--port", default="5001")
    serve_parser.add_argument("-P", "--public", action="store_true")
    serve_parser.add_argument("--tls", action="store_true", help="Not implemented by this CLI")
    serve_parser.add_argument("--tls-cert", default="")
    serve_parser.add_argument("--tls-key", default="")
    serve_parser.add_argument(
        "--log-stdout",
        action="store_true",
        help="Also write logs to stdout",
    )
    serve_parser.add_argument(
        "--debug-log",
        action="store_true",
        help="Enable verbose debug logging",
    )
    serve_parser.set_defaults(handler=serve_command)

    epg_parser = subcommands.add_parser("epg")
    epg_subcommands = epg_parser.add_subparsers(dest="epg_command")
    epg_generate = epg_subcommands.add_parser("generate", aliases=["gen", "g"])
    epg_generate.set_defaults(handler=generate_epg_command)
    epg_delete = epg_subcommands.add_parser("delete", aliases=["del", "d"])
    epg_delete.set_defaults(handler=delete_epg_command)

    login_parser = subcommands.add_parser("login")
    login_subcommands = login_parser.add_subparsers(dest="login_command")
    login_otp = login_subcommands.add_parser("otp")
    login_otp.set_defaults(handler=login_otp_command)
    login_reset = login_subcommands.add_parser("reset", aliases=["logout", "lo"])
    login_reset.set_defaults(handler=logout_command)

    background = subcommands.add_parser("background", aliases=["bg"])
    background_subcommands = background.add_subparsers(dest="background_command")
    bg_start = background_subcommands.add_parser("start", aliases=["run", "r"])
    bg_start.add_argument("-a", "--args", default="")
    bg_start.set_defaults(handler=background_start_command)
    bg_stop = background_subcommands.add_parser("stop", aliases=["kill", "k"])
    bg_stop.set_defaults(handler=background_stop_command)

    return parser


def bootstrap(config_path: str) -> None:
    cfg.load(config_path)
    init_logger()
    store.init()
    secure_url.init()


def serve_command(args: argparse.Namespace) -> int:
    host = "::" if args.public else args.host
    if args.tls:
        raise SystemExit("TLS is not implemented by the jio_py CLI")
    server.initialize(
        args.config,
        log_stdout=getattr(args, "log_stdout", False),
        debug_log=getattr(args, "debug_log", False),
    )
    server.serve(host, int(args.port))
    return 0


def generate_epg_command(args: argparse.Namespace) -> int:
    bootstrap(args.config)
    epg_path = Path("epg.xml.gz")
    if epg_path.exists():
        epg_path.unlink()
    epg.gen_xml_gz(epg_path)
    scheduler.stop()
    return 0


def delete_epg_command(args: argparse.Namespace) -> int:
    bootstrap(args.config)
    path = Path("epg.xml.gz")
    if path.exists():
        path.unlink()
        print("EPG file deleted")
    else:
        print("EPG file does not exist")
    return 0


def login_otp_command(args: argparse.Namespace) -> int:
    bootstrap(args.config)
    mobile_number = input("Enter your mobile number: +91 ").strip()
    number = "+91" + mobile_number
    print("Sending OTP to your mobile number")
    if login_send_otp(number):
        otp = input("Enter OTP: ").strip()
        result = login_verify_otp(number, otp)
        print("Login successful" if result.get("status") == "success" else "Login failed")
    return 0


def logout_command(args: argparse.Namespace) -> int:
    bootstrap(args.config)
    logout()
    print("We have successfully logged you out. Please login again.")
    return 0


def background_start_command(args: argparse.Namespace) -> int:
    bootstrap(args.config)
    command = [sys.executable, "-m", "jio_py", "--config", args.config, "serve"]
    command.extend(args.args.split())
    process = subprocess.Popen(command, start_new_session=True)
    pid_path().write_text(str(process.pid), encoding="utf-8")
    print("JIO_tv server started successfully in background.")
    return 0


def background_stop_command(args: argparse.Namespace) -> int:
    bootstrap(args.config)
    pid_file = pid_path()
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    os.kill(pid, signal.SIGTERM)
    pid_file.unlink(missing_ok=True)
    print("JIO_tv server stopped successfully.")
    return 0


def pid_path() -> Path:
    return get_path_prefix() / PID_FILE_NAME
