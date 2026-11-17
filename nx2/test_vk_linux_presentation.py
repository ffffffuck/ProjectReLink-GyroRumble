"""Check the opt-in Linux FSR and AMD Anti-Lag integration contracts.

Run with: python3 -I -B nx2/test_vk_linux_presentation.py
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def main() -> None:
    fsr = (ROOT / "gpu" / "vk" / "vk_fsr.cpp").read_text(encoding="utf-8")
    start = function(fsr, "void fsr_start()")
    assert 'getenv("RX_VK_FSR")' in start
    assert 'getenv("RX_VK_FSR_LIB")' in start
    assert "dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL)" in start
    assert "dlsym(api.dll, \"ffxCreateContext\")" in start
    assert "g_why.store(FSR_OK, std::memory_order_release)" in start

    reflex = (ROOT / "gpu" / "vk" / "vk_reflex.cpp").read_text(encoding="utf-8")
    enable = function(reflex, "void reflex_enable_implicit_layer()")
    env_check = 'getenv("RX_VK_REFLEX")'
    layer_enable = 'setenv("ENABLE_LAYER_MESA_ANTI_LAG", "1", 0)'
    assert enable.count(env_check) == 1
    assert enable.count(layer_enable) == 1
    assert enable.index(env_check) < enable.index(layer_enable)
    assert "atoi(e) <= 0" in enable
    assert "!framegen_on()" in function(reflex, "bool on_amd()")

    main_source = (ROOT / "gpu" / "vk" / "vk_main.cpp").read_text(encoding="utf-8")
    create = function(main_source, "bool create_instance()")
    assert create.index("reflex_enable_implicit_layer();") < create.index("vkCreateInstance")

    print("vk_linux_presentation: ok")


if __name__ == "__main__":
    main()
