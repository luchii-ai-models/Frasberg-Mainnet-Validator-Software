# Auth testing
1. mongosh test_database: db.users.find({role:"admin"}) -> bcrypt hash starts with $2b$; indexes users.email unique, sessions.sid, login_attempts.identifier
2. curl -X POST $API/api/auth/login -H "Content-Type: application/json" -d '{"email":"admin@frasbergai.com","password":"Federal1+"}' -> token
3. curl -H "Authorization: Bearer $TOKEN" $API/api/auth/me
4. Protected console endpoints without token -> 401 AUTH_REQUIRED; public GETs -> 200
