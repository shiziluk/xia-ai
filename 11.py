from PIL import Image

# 打开原始图片
img = Image.open("icon-400.jpg")  # 你的文件名

# 保存为 192x192
img.resize((192, 192), Image.Resampling.LANCZOS).save("icon-192.png")

# 保存为 400x400
img.resize((400, 400), Image.Resampling.LANCZOS).save("icon-400.png")

# 保存为 512x512
img.resize((512, 512), Image.Resampling.LANCZOS).save("icon-512.png")

print("✅ 已生成 icon-192.png、icon-400.png 和 icon-512.png")