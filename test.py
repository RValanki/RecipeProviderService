import boto3
import json

FUNCTION_NAME = "RecipeStack-RecipeProcessorE3C6647C-JuxFqvSBHSNc"


def invoke_recipe_processor(user_input: str) -> dict:
    client = boto3.client("lambda", region_name="ap-southeast-2")

    response = client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps({"input": user_input})
    )

    payload = json.loads(response["Payload"].read())

    if response.get("FunctionError"):
        raise RuntimeError(payload.get("errorMessage", "Unknown error"))

    return json.loads(payload.get("body", "{}"))


if __name__ == "__main__":
    print("===============================")
    print("  Recipe Processor - Test CLI")
    print("===============================")
    print()
    print("Paste your input (TikTok URL, web URL, or plain text):")
    print("(Press Enter twice when done)")
    print()

    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)

    user_input = "\n".join(lines)

    print()
    print("Processing...")
    print()

    recipe = invoke_recipe_processor(user_input)
    print(json.dumps(recipe, indent=2, ensure_ascii=False))