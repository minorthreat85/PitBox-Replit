"""
CLI command implementations for PitBox Controller.
"""
import asyncio
import logging
import sys
from typing import Optional
import httpx

from controller.config import get_config


logger = logging.getLogger(__name__)


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


async def agent_request(agent_id: str, endpoint: str, method: str = "GET", json_data: Optional[dict] = None) -> dict:
    """Make a request to an agent."""
    config = get_config()
    
    # Find agent
    agent_cfg = next((a for a in config.agents if a.id == agent_id), None)
    if agent_cfg is None:
        raise ValueError(f"Agent '{agent_id}' not found in config")
    
    base_url = f"http://{agent_cfg.host}:{agent_cfg.port}"
    headers = {"Authorization": f"Bearer {agent_cfg.token}"}
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        if method == "GET":
            resp = await client.get(f"{base_url}{endpoint}", headers=headers)
        elif method == "POST":
            resp = await client.post(f"{base_url}{endpoint}", headers=headers, json=json_data or {})
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        resp.raise_for_status()
        return resp.json()


def cli_status():
    """Print status of all agents."""
    config = get_config()
    
    print("\nAgent Status:")
    print("-" * 80)
    print(f"{'Agent ID':<15} {'Online':<10} {'AC Running':<15} {'PID':<10}")
    print("-" * 80)
    
    async def get_all_status():
        tasks = []
        for agent in config.agents:
            tasks.append(check_agent_status(agent.id))
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def check_agent_status(agent_id: str):
        try:
            # Ping first
            agent_cfg = next((a for a in config.agents if a.id == agent_id), None)
            base_url = f"http://{agent_cfg.host}:{agent_cfg.port}"
            headers = {"Authorization": f"Bearer {agent_cfg.token}"}
            
            async with httpx.AsyncClient(timeout=2.0) as client:
                ping_resp = await client.get(f"{base_url}/ping")
                if ping_resp.status_code != 200:
                    return {"agent_id": agent_id, "online": False}
                
                status_resp = await client.get(f"{base_url}/status", headers=headers)
                status_resp.raise_for_status()
                return status_resp.json()
        except Exception:
            return {"agent_id": agent_id, "online": False}
    
    results = run_async(get_all_status())
    
    for result in results:
        if isinstance(result, Exception):
            print(f"{'ERROR':<15} {'N/A':<10} {'N/A':<15} {'N/A':<10}")
        else:
            agent_id = result.get("agent_id", "?")
            online = "Online" if result.get("online", True) else "Offline"
            ac_running = "Running" if result.get("ac_running", False) else "Stopped"
            pid = str(result.get("pid", "-"))
            print(f"{agent_id:<15} {online:<10} {ac_running:<15} {pid:<10}")
    
    print("-" * 80)
    print()


def cli_start(sim_id: Optional[str], all_sims: bool):
    """Start sim(s)."""
    config = get_config()
    
    if all_sims:
        sim_ids = [agent.id for agent in config.agents]
    elif sim_id:
        sim_ids = [sim_id]
    else:
        print("ERROR: Must specify --sim or --all")
        sys.exit(1)
    
    print(f"\nStarting {len(sim_ids)} sim(s)...")
    
    async def start_all():
        tasks = []
        for sid in sim_ids:
            tasks.append(agent_request(sid, "/start", method="POST"))
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    results = run_async(start_all())
    
    for sid, result in zip(sim_ids, results):
        if isinstance(result, Exception):
            print(f"  {sid}: FAILED - {result}")
        else:
            msg = result.get("message", "Unknown")
            print(f"  {sid}: {msg}")
    
    print()


def cli_stop(sim_id: Optional[str], all_sims: bool):
    """Stop sim(s)."""
    config = get_config()
    
    if all_sims:
        sim_ids = [agent.id for agent in config.agents]
    elif sim_id:
        sim_ids = [sim_id]
    else:
        print("ERROR: Must specify --sim or --all")
        sys.exit(1)
    
    print(f"\nStopping {len(sim_ids)} sim(s)...")
    
    async def stop_all():
        tasks = []
        for sid in sim_ids:
            tasks.append(agent_request(sid, "/stop", method="POST"))
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    results = run_async(stop_all())
    
    for sid, result in zip(sim_ids, results):
        if isinstance(result, Exception):
            print(f"  {sid}: FAILED - {result}")
        else:
            msg = result.get("message", "Unknown")
            print(f"  {sid}: {msg}")
    
    print()


def cli_apply_steering(sim_id: str, preset_name: str):
    """Apply steering preset."""
    print(f"\nApplying steering preset '{preset_name}' to {sim_id}...")
    
    try:
        result = run_async(agent_request(
            sim_id,
            "/apply_steering_preset",
            method="POST",
            json_data={"name": preset_name}
        ))
        print(f"  Success: {result.get('message', 'Applied')}")
    except Exception as e:
        print(f"  FAILED: {e}")
    
    print()


def cli_apply_assists(sim_id: str, preset_name: str):
    """Apply assists preset."""
    print(f"\nApplying assists preset '{preset_name}' to {sim_id}...")
    
    try:
        result = run_async(agent_request(
            sim_id,
            "/apply_assists_preset",
            method="POST",
            json_data={"name": preset_name}
        ))
        print(f"  Success: {result.get('message', 'Applied')}")
    except Exception as e:
        print(f"  FAILED: {e}")
    
    print()


def cli_presets(sim_id: str):
    """List available presets for a sim."""
    print(f"\nAvailable presets for {sim_id}:")
    
    try:
        result = run_async(agent_request(sim_id, "/presets"))
        
        print("\n  Steering presets:")
        for preset in result.get("steering", []):
            print(f"    - {preset}")
        
        print("\n  Assists presets:")
        for preset in result.get("assists", []):
            print(f"    - {preset}")
        
    except Exception as e:
        print(f"  FAILED: {e}")
    
    print()
