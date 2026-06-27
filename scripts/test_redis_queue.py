import json
import time
import redis

def main():
    r = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    task_id = f"manual_test_{int(time.time())}"
    task = {
        "task_id": task_id,
        "command": "git-status",
        "intent": "git",
        "args": []
    }
    print("Pushing task:", task)
    r.lpush("hermes:tasks", json.dumps(task, ensure_ascii=False))
    
    print("Waiting for result...")
    for i in range(15):
        data = r.get(f"hermes:result:{task_id}")
        if data:
            print("Received result successfully!")
            result_json = json.loads(data)
            print(json.dumps(result_json, indent=2, ensure_ascii=False))
            break
        time.sleep(1)
    else:
        print("TIMEOUT - NO RESULT")

if __name__ == "__main__":
    main()
