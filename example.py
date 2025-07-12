import os
import re

def process_tex_file(filepath):
    """
    处理单个 .tex 文件，查找并替换题目格式。
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = re.compile(
        r'(\d+)\．(.*?)'  # 题号和题干
        r'\n(?:\s*A．(.*?))' # 选项 A
        r'\n(?:\s*B．(.*?))' # 选项 B
        r'\n(?:\s*C．(.*?))' # 选项 C
        r'\n(?:\s*D．(.*?))', # 选项 D
        re.DOTALL # 允许 . 匹配换行符
    )

    def replacement_func(match):
        problem_number = match.group(1)
        stem = match.group(2).strip()
        option_a = match.group(3).strip()
        option_b = match.group(4).strip()
        option_c = match.group(5).strip()
        option_d = match.group(6).strip()

        # 如果题号是 '1' 或者 '2' (根据您的示例判断，您可能希望所有题目都转换)
        # 如果只想转换特定题号，这里可以添加条件，例如：
        # if problem_number in ['1', '2']:
        # else: return match.group(0)
        
        # 构建新的格式
        new_format = (
            f'{stem} （\\qquad）\n'
            '\\begin{tasks}(4)\n'
            f'    \\task ${option_a}$\n'  # 选项内容通常放在数学模式中
            f'    \\task ${option_b}$\n'
            f'    \\task ${option_c}$\n'
            f'    \\task ${option_d}$\n'
            '\\end{tasks}'
        )
        return new_format

    new_content = pattern.sub(replacement_func, content)

    if new_content != content:
        print(f"检测到并修改文件: {filepath}")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)

def recursive_find_and_process(root_dir):
    """
    递归查找指定目录下的所有 .tex 文件并进行处理。
    """
    print(f"开始在目录 '{root_dir}' 中递归查找 .tex 文件...")
    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if filename.endswith('.tex'):
                filepath = os.path.join(dirpath, filename)
                process_tex_file(filepath)
    print("所有 .tex 文件处理完毕。")

if __name__ == "__main__":
    target_directory = '.'
    recursive_find_and_process(target_directory)