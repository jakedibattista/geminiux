import os
from google.adk.agents import LlmAgent, SequentialAgent, ParallelAgent
from google.adk.tools.computer_use.computer_use_toolset import ComputerUseToolset
from browser.playwright_computer import PlaywrightComputer
from .persona_agent import make_persona_agent
from .consolidator import make_consolidator_agent

# Default stub required by ADK CLI/Web UI, though we instantiate dynamically in main.py
root_agent = SequentialAgent(
    name="PlaceholderAgent",
    sub_agents=[]
)

def build_root_agent(persona_ids: list, audit_id: str, url: str, custom_personas: list = None, auth: dict = None):
    """
    Dynamically builds the agent tree based on selected personas.
    auth (optional): dict with keys loginUrl, loginEmail, loginPassword.
    """
    if custom_personas is None:
        custom_personas = []

    # 1. Create the individual persona agents using the Computer Use Toolset
    persona_agents = []
    for pid in persona_ids:
        custom_data = next((p for p in custom_personas if p.get('id') == pid), None)
        agent = make_persona_agent(pid, audit_id, url, custom_data, auth=auth)
        persona_agents.append(agent)
        
    # 2. Group them in a ParallelAgent so they browse concurrently
    parallel_execution = ParallelAgent(
        name="ParallelPersonas",
        sub_agents=persona_agents
    )
    
    # 3. Create the consolidator that reads their outputs
    consolidator = make_consolidator_agent(audit_id)
    
    # 4. Return the Sequential workflow
    return SequentialAgent(
        name="AuditWorkflow",
        sub_agents=[
            parallel_execution,
            consolidator
        ]
    )