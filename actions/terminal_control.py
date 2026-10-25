# actions/terminal_control.py
import subprocess
import os

def run_terminal_command(parameters: dict, player=None) -> str:
    """
    Executes a terminal command on the user's laptop using subprocess.
    Works on Windows, macOS, and Linux.
    """
    command = parameters.get("command", "").strip()
    if not command:
        return "Sir, no command was specified to execute."

    if player:
        player.write_log(f"[cmd] Executing: {command}")

    try:
        # Run the command with a 30s timeout
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        code = result.returncode

        output_parts = [f"Command exited with code: {code}"]
        if stdout:
            output_parts.append(f"STDOUT:\n{stdout.strip()}")
        if stderr:
            output_parts.append(f"STDERR:\n{stderr.strip()}")
        
        return "\n\n".join(output_parts) or "Command executed successfully with no output."
        
    except subprocess.TimeoutExpired:
        return "Sir, the command execution timed out after 30 seconds."
    except Exception as e:
        return f"Sir, I encountered an error while executing the command: {e}"
