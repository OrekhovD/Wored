# Model Inventory Playbook

## Purpose
This playbook describes how to perform a model inventory audit to discover available AI providers and models without exposing secrets.

## Context
WORED/Hermes needs to know which AI providers are available and what models they support. This audit helps determine the current model capabilities and prepare for implementing model routing strategies.

## Process

### Step 1: Check for Credentials
1. Look for credential files in standard locations:
   - `~/.hermes/.env`
   - `~/.hermes/auth.json`
   - `~/.hermes/secrets/nvidia_keys.txt`
   - `/mnt/d/WORED/.env`

2. Check for environment variables without exposing values:
   - DASHSCOPE_API_KEY
   - ZAI_API_KEY
   - GLM_API_KEY
   - MINIMAX_API_KEY
   - NVIDIA_API_KEY
   - OPENROUTER_API_KEY

### Step 2: Test Each Provider
1. **NVIDIA NIM**:
   - Check for NVIDIA API keys in secrets file
   - Test with minimaxai/minimax-m2.7 model
   - Record response time and status

2. **OpenRouter**:
   - Check for OPENROUTER_API_KEY in hermes env
   - Test with qwen/qwen-3-coder-plus model
   - Record response time and status

3. **DashScope (Qwen)**:
   - Check for DASHSCOPE_API_KEY in environment
   - Test with qwen3-coder-plus model
   - Record response time and status

4. **ZAI/GLM**:
   - Check for ZAI_API_KEY or GLM_API_KEY in environment
   - Test with glm-4-plus model
   - Record response time and status

### Step 3: Analyze Results
1. Identify which providers are accessible
2. Note response times and reliability
3. Document authentication issues
4. Assess overall model availability

### Step 4: Plan Model Roles
Based on inventory results, assign roles to models:
- Primary coding: Most capable for code generation
- Reviewer: For code review and complex reasoning
- Fallback: Available when primary fails
- Fast diagnostics: For quick tasks
- Long context: For document processing

## Implementation Guidelines

### Security
- Never expose API key values in logs or output
- Mask key prefixes (e.g., nvapi-***, sk-***)
- Store credentials securely in appropriate locations

### Reliability
- Implement credential pools for each provider
- Use fallback chains when primary provider fails
- Add circuit breaker pattern for handling rate limits

### Performance
- Track response times for each provider
- Use fastest model suitable for task when possible
- Implement caching for repeated requests

## Expected Output
A JSON report containing:
- Available providers and their status
- Response times
- Error conditions
- Credential information (masked)
- Recommendations for model roles

## Validation
1. Run the inventory script: `python3 scripts/hermes/model_inventory.py`
2. Verify JSON output contains all required fields
3. Confirm no sensitive information is exposed
4. Check that status accurately reflects provider availability

## Troubleshooting
- If all providers show "no_key", verify credentials are properly set
- If providers show connection timeouts, check network connectivity
- If authentication errors occur, verify API key validity
- If synthetic fallback is needed, use `--allow-synthetic` flag