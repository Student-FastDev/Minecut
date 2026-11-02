(function(global) {
    function CSInterface() {}
    CSInterface.prototype.evalScript = function(script, callback) {
        if (!global.__adobe_cep__ || !global.__adobe_cep__.evalScript) {
            if (callback) callback("EvalScript_ErrMessage");
            return;
        }
        global.__adobe_cep__.evalScript(script, callback || function() {});
    };
    CSInterface.prototype.getSystemPath = function(kind) {
        return global.__adobe_cep__ && global.__adobe_cep__.getSystemPath ? global.__adobe_cep__.getSystemPath(kind) : "";
    };
    global.CSInterface = CSInterface;
})(window);