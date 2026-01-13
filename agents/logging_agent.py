# agents/05_logging_agent.py
"""
05. LoggingAgent - 로깅 및 터미널 출력 에이전트

통일된 로깅과 아름다운 터미널 출력을 제공합니다.

역할:
- 에이전트별 로그 포맷팅
- 진행 상태 표시
- 결과 요약 출력 (Rich UI)
- 에러 메시지 강조
- 작업 히스토리 표시
"""

from typing import Dict, Any, List
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.tree import Tree
from rich.live import Live
from langchain_ollama import ChatOllama
from llm_factory import get_shared_llm
from .base import BaseAgent


class LoggingAgent(BaseAgent):
    """
    통일된 로깅 및 터미널 출력 에이전트

    작업:
    1. 에이전트별 로그 포맷팅
    2. 진행 상태 표시
    3. 결과 요약 출력
    4. 에러 메시지 강조
    """

    def __init__(self, llm: ChatOllama = None):
        super().__init__(
            name="LoggingAgent",
            description="Unified logging and terminal output formatting",
        )
        self.console = Console()
        self.logs: List[Dict[str, Any]] = []
        
        # LLM for error message conversion (싱글톤 공유 인스턴스 사용)
        self.llm = llm or get_shared_llm()

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        작업 실행

        Expected task actions:
            - log: 일반 로그
            - log_step: 단계 로그 (에이전트 작업)
            - log_result: 최종 결과 출력
            - log_error: 에러 출력
            - show_progress: 진행률 표시
        """
        action = task.get("action")
        data = task.get("data", {})

        try:
            if action == "log":
                self._log_message(data.get("message", ""), data.get("level", "info"))

            elif action == "log_step":
                self._log_agent_step(
                    data.get("agent_name", "Unknown"),
                    data.get("step", ""),
                    data.get("status", "running"),
                )

            elif action == "log_result":
                self._log_final_result(data.get("result", {}))

            elif action == "log_error":
                self._log_error(
                    data.get("error", "Unknown error"), data.get("agent_name", "System")
                )

            elif action == "show_summary":
                self._show_task_summary(data.get("tasks", []))

            return {"success": True, "agent": self.name}

        except Exception as e:
            return {"success": False, "error": str(e), "agent": self.name}

    def _log_message(self, message: str, level: str = "info"):
        """일반 로그 메시지 출력"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        styles = {
            "info": ("ℹ️", "cyan"),
            "success": ("✅", "green"),
            "error": ("❌", "red"),
            "warning": ("⚠️", "yellow"),
            "debug": ("🔍", "dim"),
        }

        icon, style = styles.get(level, ("•", "white"))

        self.console.print(f"[{timestamp}] {icon} {message}", style=style)

        # 로그 저장
        self.logs.append({"timestamp": timestamp, "level": level, "message": message})

    def _log_agent_step(self, agent_name: str, step: str, status: str = "running"):
        """에이전트 단계 로그"""
        status_icons = {
            "running": "⚙️",
            "completed": "✅",
            "failed": "❌",
            "pending": "⏳",
        }

        icon = status_icons.get(status, "•")

        self.console.print(
            f"  {icon} [{agent_name}] {step}",
            style="bold" if status == "running" else "",
        )

    def _log_final_result(self, result: Dict[str, Any]):
        """최종 결과를 패널로 출력"""
        if result.get("success", False):
            data = result.get("data", {})

            # Build image list
            images = data.get('images', [])
            thumbnail = data.get('thumbnail')
            
            image_section = ""
            if thumbnail:
                thumb_name = thumbnail.get('local_path', '').split('/')[-1] if isinstance(thumbnail, dict) else str(thumbnail).split('/')[-1]
                image_section += f"\n[bold]Thumbnail:[/bold]\n  📷 {thumb_name}"
            
            if images:
                image_section += f"\n\n[bold]Images ({len(images)}):[/bold]"
                for img in images[:5]:  # Show max 5
                    img_name = img.get('local_path', '').split('/')[-1] if isinstance(img, dict) else str(img)
                    s3_url = img.get('s3_url', 'pending') if isinstance(img, dict) else 'pending'
                    image_section += f"\n  🖼️ {img_name}"
                    if s3_url and s3_url != 'pending':
                        image_section += f" → [dim]{s3_url[:50]}...[/dim]"
                if len(images) > 5:
                    image_section += f"\n  ... and {len(images) - 5} more"
            
            # Build tags section
            metadata = data.get('extracted_metadata', {})
            tags = metadata.get('tags', [])
            tags_str = ', '.join(tags[:6]) if tags else 'N/A'
            if len(tags) > 6:
                tags_str += f" (+{len(tags) - 6} more)"
            
            # Build categories section  
            categories = data.get('categories', []) or metadata.get('categories', [])
            cat_str = ' > '.join(categories) if categories else data.get('category', 'N/A')
            
            # Build post properties section
            user_id = metadata.get('user_id') or data.get('user_id', 'N/A')
            username = metadata.get('username') or data.get('username', 'N/A')
            status = metadata.get('status') or data.get('status', 'N/A')
            description = metadata.get('description') or metadata.get('summary', '')
            desc_preview = (description[:80] + '...') if len(description) > 80 else description
            word_count = metadata.get('word_count', 'N/A')
            reading_time = metadata.get('reading_time', 'N/A')
            date = metadata.get('date', 'N/A')

            content = f"""[bold green]✅ Task Completed Successfully![/bold green]

[bold]Article Details:[/bold]
  • File: {data.get('file_name', 'N/A')}
  • Title: {data.get('title', 'N/A')}
  • Slug: {data.get('slug', 'N/A')}
  • Article ID: {data.get('article_id', 'N/A')}

[bold]Categories:[/bold]
  📂 {cat_str}

[bold]Tags:[/bold]
  🏷️ {tags_str}
{image_section}

[bold]Post Properties:[/bold]
  • userId: {user_id}
  • username: {username}
  • status: {status}
  • date: {date}
  • wordCount: {word_count}
  • readingTime: {reading_time} min
  • description: [dim]{desc_preview}[/dim]

[bold]Published URL:[/bold]
  {data.get('published_url', 'N/A')}
"""

            self.console.print(
                Panel(
                    content,
                    title="📝 Blog Post Published",
                    border_style="green",
                    padding=(1, 2),
                )
            )

            # Print full upload payload
            upload_payload = data.get('upload_payload')
            if upload_payload:
                self._print_upload_payload(upload_payload)
        else:
            error = result.get("error", "Unknown error")
            self.console.print(
                Panel(
                    f"[bold red]❌ Task Failed[/bold red]\n\n{error}",
                    title="Error",
                    border_style="red",
                    padding=(1, 2),
                )
            )

    def _print_upload_payload(self, payload: dict):
        """Print the full upload payload as formatted JSON."""
        import json
        from rich.syntax import Syntax

        # Deep copy and truncate content for display
        display_payload = payload.copy()
        if 'content' in display_payload and len(str(display_payload.get('content', ''))) > 100:
            display_payload['content'] = str(display_payload['content'])[:100] + '...'
        
        # Truncate nested content in metadata if present
        if 'metadata' in display_payload and isinstance(display_payload['metadata'], dict):
            meta = display_payload['metadata'].copy()
            display_payload['metadata'] = meta

        # Format as JSON
        json_str = json.dumps(display_payload, indent=2, default=str, ensure_ascii=False)

        # Use Rich Syntax for JSON highlighting
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)

        self.console.print()
        self.console.print(
            Panel(
                syntax,
                title="📦 Upload Payload",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    def _log_error(self, error: str, agent_name: str = "System"):
        """에러 메시지 출력 (LLM으로 사용자 친화적 메시지 변환)"""
        # Try to convert error message to user-friendly format
        friendly_error = self._convert_error_message(error)
        
        self.console.print(
            Panel(
                f"[bold red]Error in {agent_name}:[/bold red]\n\n{friendly_error}",
                border_style="red",
                padding=(1, 2),
            )
        )
    
    def _convert_error_message(self, error: str) -> str:
        """
        Use Qwen3 to convert technical error messages to user-friendly format.
        Falls back to original error if conversion fails.
        """
        # Skip conversion for short/simple errors
        if len(error) < 30:
            return error
        
        try:
            prompt = f"""Convert this technical error message into a simple, user-friendly explanation in 1-2 sentences. 
Keep it concise and actionable. If it's already clear, just rephrase slightly.

Error: {error}

User-friendly explanation:"""
            
            response = self.llm.invoke(prompt)
            friendly = response.content.strip()
            
            # Return converted message with original for reference
            if friendly and len(friendly) > 10:
                return f"{friendly}\n\n[dim]Original: {error}[/dim]"
            return error
            
        except Exception as e:
            # Fallback to original error
            return error

    def _show_task_summary(self, tasks: List[Dict[str, Any]]):
        """작업 목록을 테이블로 표시"""
        if not tasks:
            self.console.print("[yellow]No tasks to display[/yellow]")
            return

        table = Table(title="📋 Task Summary", show_header=True)
        table.add_column("ID", style="cyan", width=8)
        table.add_column("Agent", style="blue", width=20)
        table.add_column("Action", style="white", width=20)
        table.add_column("Status", width=12)
        table.add_column("Duration", style="dim", width=10)

        for task in tasks:
            status = task.get("status", "unknown")
            status_style = {
                "completed": "[green]✅ Done[/green]",
                "failed": "[red]❌ Failed[/red]",
                "running": "[yellow]⚙️ Running[/yellow]",
                "pending": "[dim]⏳ Pending[/dim]",
            }.get(status, status)

            duration = task.get("duration", 0)
            duration_str = f"{duration:.2f}s" if duration else "-"

            table.add_row(
                task.get("task_id", "N/A"),
                task.get("agent_name", "N/A"),
                task.get("action", "N/A"),
                status_style,
                duration_str,
            )

        self.console.print(table)

    def show_agent_tree(self, agents: List[Dict[str, Any]]):
        """에이전트 구조를 트리로 표시"""
        tree = Tree("🤖 [bold]Multi-Agent System[/bold]")

        for agent in agents:
            agent_branch = tree.add(
                f"[cyan]{agent['name']}[/cyan] - {agent.get('status', 'idle')}"
            )
            if agent.get("description"):
                agent_branch.add(f"[dim]{agent['description']}[/dim]")

        self.console.print(tree)

    def show_progress_bar(self, total: int, description: str = "Processing"):
        """진행률 바 표시 (컨텍스트 매니저로 사용)"""
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=self.console,
        )

    def clear_console(self):
        """콘솔 클리어"""
        self.console.clear()

    def print_banner(self, title: str, subtitle: str = ""):
        """배너 출력"""
        banner = f"""[bold cyan]{title}[/bold cyan]"""
        if subtitle:
            banner += f"\n[dim]{subtitle}[/dim]"

        self.console.print(Panel(banner, border_style="cyan", padding=(1, 2)))
