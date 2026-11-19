#!/usr/bin/env python3
"""
Test script for new grayspace-web features:
- Traffic shaping
- Console access
- Network (vNET/Zone) control
- Reset operations
"""

import sys
from pathlib import Path

print("=" * 80)
print("TESTING NEW GRAYSPACE-WEB FEATURES")
print("=" * 80)
print()

# Test 1: Import all new models
print("TEST 1: Import new models")
print("-" * 40)
try:
    from models.traffic import TrafficConfig
    print("  ✓ TrafficConfig imported")

    print("TEST 1: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 2: Validate TrafficConfig model
print("TEST 2: Validate TrafficConfig model")
print("-" * 40)
try:
    # Test valid config
    config = TrafficConfig(latency_ms=100, jitter_ms=20, packet_loss_percent=5)
    assert config.latency_ms == 100
    assert config.jitter_ms == 20
    assert config.packet_loss_percent == 5
    print("  ✓ TrafficConfig accepts valid values")

    # Test has_any_config
    assert config.has_any_config()
    print("  ✓ has_any_config() returns True for active config")

    empty_config = TrafficConfig()
    assert not empty_config.has_any_config()
    print("  ✓ has_any_config() returns False for empty config")

    # Test validation bounds
    try:
        invalid = TrafficConfig(latency_ms=20000)  # Max is 10000
        print("  ✗ Should have rejected latency > 10000")
        sys.exit(1)
    except Exception:
        print("  ✓ Rejects latency > 10000ms")

    print("TEST 2: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Import SSH client
print("TEST 3: Import SSH client service")
print("-" * 40)
try:
    from services.ssh_client import ssh_client
    print("  ✓ ssh_client singleton imported")

    # Verify methods exist
    assert hasattr(ssh_client, 'execute_command')
    assert hasattr(ssh_client, 'execute_command_stream')
    assert hasattr(ssh_client, 'create_interactive_session')
    print("  ✓ ssh_client has expected methods")

    print("TEST 3: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 4: Import traffic service
print("TEST 4: Import traffic service")
print("-" * 40)
try:
    from services.traffic_service import TrafficService, traffic_service
    print("  ✓ TrafficService imported")
    print("  ✓ traffic_service singleton imported")

    # Verify methods exist
    assert hasattr(traffic_service, 'get_interfaces')
    assert hasattr(traffic_service, 'apply_traffic_config')
    assert hasattr(traffic_service, 'clear_traffic_config')
    print("  ✓ traffic_service has expected methods")

    # Test tc command builder
    service = TrafficService()
    config = TrafficConfig(latency_ms=100, jitter_ms=20, packet_loss_percent=5)
    commands = service._build_tc_commands("eth0", config)
    assert len(commands) > 0
    assert "netem" in commands[0]
    assert "delay" in commands[0] and "100" in commands[0]
    assert "loss" in commands[0] and "5" in commands[0]
    print("  ✓ _build_tc_commands generates correct netem command")

    print("TEST 4: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 5: Import console service
print("TEST 5: Import console service")
print("-" * 40)
try:
    from services.console_service import console_manager
    print("  ✓ console_manager singleton imported")

    # Verify methods exist
    assert hasattr(console_manager, 'create_session')
    assert hasattr(console_manager, 'get_session')
    assert hasattr(console_manager, 'close_session')
    assert hasattr(console_manager, 'start')
    assert hasattr(console_manager, 'stop')
    print("  ✓ console_manager has expected methods")

    print("TEST 5: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 6: Import network service
print("TEST 6: Import network service")
print("-" * 40)
try:
    from services.network_service import network_service
    print("  ✓ network_service singleton imported")

    # Verify methods exist
    assert hasattr(network_service, 'get_zones')
    assert hasattr(network_service, 'get_vnets')
    assert hasattr(network_service, 'change_vnet_zone')
    assert hasattr(network_service, 'get_link_network_info')
    print("  ✓ network_service has expected methods")

    print("TEST 6: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 7: Import reset service
print("TEST 7: Import reset service")
print("-" * 40)
try:
    from services.reset_service import ResetOperation, ResetStatus, reset_service
    print("  ✓ ResetOperation enum imported")
    print("  ✓ ResetStatus enum imported")
    print("  ✓ reset_service singleton imported")

    # Verify methods exist
    assert hasattr(reset_service, 'run_terraform_apply')
    assert hasattr(reset_service, 'run_ansible_playbook')
    assert hasattr(reset_service, 'run_full_reset')
    assert hasattr(reset_service, 'get_status')
    assert hasattr(reset_service, 'cancel_operation')
    print("  ✓ reset_service has expected methods")

    # Verify enums
    assert ResetOperation.TERRAFORM.value == "terraform"
    assert ResetOperation.ANSIBLE.value == "ansible"
    assert ResetOperation.FULL.value == "full"
    print("  ✓ ResetOperation enum values correct")

    assert ResetStatus.IDLE.value == "idle"
    assert ResetStatus.RUNNING.value == "running"
    assert ResetStatus.COMPLETED.value == "completed"
    assert ResetStatus.FAILED.value == "failed"
    print("  ✓ ResetStatus enum values correct")

    # Test status
    status = reset_service.get_status()
    assert "status" in status
    assert "is_running" in status
    print("  ✓ get_status() returns expected structure")

    print("TEST 7: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 8: Import all routers
print("TEST 8: Import all routers")
print("-" * 40)
try:
    from routers import traffic, console, networks, reset
    print("  ✓ traffic router imported")
    print("  ✓ console router imported")
    print("  ✓ networks router imported")
    print("  ✓ reset router imported")

    # Verify each has a router object
    assert hasattr(traffic, 'router')
    assert hasattr(console, 'router')
    assert hasattr(networks, 'router')
    assert hasattr(reset, 'router')
    print("  ✓ All routers have APIRouter instances")

    print("TEST 8: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 9: Verify main.py imports new routers
print("TEST 9: Verify main.py imports new routers")
print("-" * 40)
try:
    main_path = Path("/workspaces/grayspace-web/backend/main.py")
    with open(main_path) as f:
        main_code = f.read()

    required_imports = [
        "traffic",
        "console",
        "networks",
        "reset"
    ]

    for router_name in required_imports:
        if router_name in main_code:
            print(f"  ✓ main.py imports {router_name} router")
        else:
            print(f"  ✗ main.py missing {router_name} router import")
            sys.exit(1)

    # Check routers are registered
    if "app.include_router(traffic.router)" in main_code:
        print("  ✓ traffic router registered with app")
    else:
        print("  ✗ traffic router not registered")
        sys.exit(1)

    if "app.include_router(console.router)" in main_code:
        print("  ✓ console router registered with app")
    else:
        print("  ✗ console router not registered")
        sys.exit(1)

    if "app.include_router(networks.router)" in main_code:
        print("  ✓ networks router registered with app")
    else:
        print("  ✗ networks router not registered")
        sys.exit(1)

    if "app.include_router(reset.router)" in main_code:
        print("  ✓ reset router registered with app")
    else:
        print("  ✗ reset router not registered")
        sys.exit(1)

    print("TEST 9: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 10: Verify console manager lifecycle in main.py
print("TEST 10: Verify console manager lifecycle in main.py")
print("-" * 40)
try:
    main_path = Path("/workspaces/grayspace-web/backend/main.py")
    with open(main_path) as f:
        main_code = f.read()

    if "console_manager.start()" in main_code:
        print("  ✓ console_manager.start() called in startup")
    else:
        print("  ✗ console_manager.start() missing from startup")
        sys.exit(1)

    if "console_manager.stop()" in main_code:
        print("  ✓ console_manager.stop() called in shutdown")
    else:
        print("  ✗ console_manager.stop() missing from shutdown")
        sys.exit(1)

    print("TEST 10: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 11: Verify nginx config has console WebSocket
print("TEST 11: Verify nginx config has console WebSocket")
print("-" * 40)
try:
    nginx_path = Path(__file__).resolve().parents[2] / "container" / "nginx.conf"
    with open(nginx_path) as f:
        nginx_code = f.read()

    if "/ws/console/" in nginx_code:
        print("  ✓ nginx config has /ws/console/ location")
    else:
        print("  ✗ nginx config missing /ws/console/ location")
        sys.exit(1)

    if "proxy_read_timeout" in nginx_code:
        print("  ✓ nginx config has proxy_read_timeout for WebSocket")
    else:
        print("  ✗ nginx config missing proxy_read_timeout")
        sys.exit(1)

    print("TEST 11: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 12: Verify frontend component files exist
print("TEST 12: Verify frontend component files exist")
print("-" * 40)
try:
    frontend_base = Path("/workspaces/grayspace-web/frontend/src")

    components = [
        "components/TrafficShapingPanel.tsx",
        "components/ConsoleTerminal.tsx",
        "components/LinkNetworkPanel.tsx",
        "components/ResetModal.tsx",
    ]

    for component in components:
        component_path = frontend_base / component
        if component_path.exists():
            print(f"  ✓ {component} exists")
        else:
            print(f"  ✗ {component} missing")
            sys.exit(1)

    print("TEST 12: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 13: Verify frontend types include new types
print("TEST 13: Verify frontend types include new types")
print("-" * 40)
try:
    types_path = Path("/workspaces/grayspace-web/frontend/src/types/index.ts")
    with open(types_path) as f:
        types_code = f.read()

    required_types = [
        "TrafficConfig",
        "InterfaceInfo",
        "ZoneInfo",
        "VNetInfo",
        "LinkNetworkInfo",
        "ResetStatus",
        "ConsoleSessionInfo",
    ]

    for type_name in required_types:
        if f"interface {type_name}" in types_code or f"export interface {type_name}" in types_code:
            print(f"  ✓ {type_name} interface defined")
        else:
            print(f"  ✗ {type_name} interface missing")
            sys.exit(1)

    print("TEST 13: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 14: Verify frontend API service has new endpoints
print("TEST 14: Verify frontend API service has new endpoints")
print("-" * 40)
try:
    api_path = Path("/workspaces/grayspace-web/frontend/src/services/api.ts")
    with open(api_path) as f:
        api_code = f.read()

    required_methods = [
        "getInterfaces",
        "applyTrafficConfig",
        "clearTrafficConfig",
        "getZones",
        "getVNets",
        "changeVNetZone",
        "getResetStatus",
        "runTerraform",
        "runAnsible",
        "runFullReset",
        "createConsoleWebSocket",
    ]

    for method in required_methods:
        if method in api_code:
            print(f"  ✓ {method}() method defined")
        else:
            print(f"  ✗ {method}() method missing")
            sys.exit(1)

    print("TEST 14: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 15: Verify package.json has xterm dependencies
print("TEST 15: Verify package.json has xterm dependencies")
print("-" * 40)
try:
    import json
    pkg_path = Path("/workspaces/grayspace-web/frontend/package.json")
    with open(pkg_path) as f:
        pkg = json.load(f)

    dependencies = pkg.get("dependencies", {})

    xterm_deps = ["xterm", "xterm-addon-fit", "xterm-addon-web-links"]
    for dep in xterm_deps:
        if dep in dependencies:
            print(f"  ✓ {dep} dependency added: {dependencies[dep]}")
        else:
            print(f"  ✗ {dep} dependency missing")
            sys.exit(1)

    print("TEST 15: PASSED\n")
except Exception as e:
    print(f"  ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Summary
print("=" * 80)
print("ALL NEW FEATURE TESTS PASSED")
print("=" * 80)
print()
print("Summary of new features verified:")
print("  Backend:")
print("    ✓ Traffic shaping models, service, and router")
print("    ✓ Console access service and WebSocket router")
print("    ✓ Network (vNET/Zone) service and router")
print("    ✓ Reset operations service and router")
print("    ✓ SSH client service for remote execution")
print("  Frontend:")
print("    ✓ TrafficShapingPanel component")
print("    ✓ ConsoleTerminal component (xterm.js)")
print("    ✓ LinkNetworkPanel component")
print("    ✓ ResetModal component")
print("    ✓ API service with all new endpoints")
print("    ✓ TypeScript types for new features")
print("  Deployment:")
print("    ✓ nginx config updated for console WebSocket")
print()
print("To test the full application:")
print("  1. cd /workspaces/grayspace-web/frontend && npm install && npm run build")
print("  2. cd /workspaces/grayspace-web/backend && uvicorn main:app --reload")
print("  3. Open http://localhost:8000 in browser")
print()
